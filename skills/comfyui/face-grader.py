#!/usr/bin/env python3
"""
Face similarity grader for LoRA training data curation.

Compares each generated image against a reference image using InsightFace
(ArcFace) embeddings and cosine similarity. Outputs a ranked report and
per-pose winner sheets.

Usage:
    python face-grader.py --reference nikki-front.png --images-dir /path/to/output/ --out /path/to/grades/

Requires: insightface, onnxruntime, opencv-python-headless, Pillow, numpy
Install:   uv pip install --python 3.11 insightface onnxruntime opencv-python-headless Pillow numpy
           (or use the venv at .venv-faceid)
"""

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


# ---------------------------------------------------------------------------
# 1. Extract face embedding
# ---------------------------------------------------------------------------

def init_face_analyzer():
    """Initialize InsightFace with ArcFace recognition model (buffalo_l)."""
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(
        name="buffalo_l",
        providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
    )
    app.prepare(ctx_id=0, det_size=(640, 640))
    return app


def get_face_embedding(app, image_path: str) -> np.ndarray | None:
    """
    Detect the largest face in an image and return its 512-dim ArcFace embedding.
    Returns None if no face is detected.
    """
    img = cv2.imread(image_path)
    if img is None:
        print(f"  [WARN] Cannot read image: {image_path}")
        return None

    faces = app.get(img)
    if not faces:
        print(f"  [WARN] No face detected: {image_path}")
        return None

    # Use the largest face (by bounding box area)
    best = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    return best.embedding


# ---------------------------------------------------------------------------
# 2. Compute cosine similarity
# ---------------------------------------------------------------------------

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors. 1.0 = identical, 0.0 = orthogonal."""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


# ---------------------------------------------------------------------------
# 3. Parse pose + variant from filename
# ---------------------------------------------------------------------------

def parse_filename(fname: str, prefix: str | None = None) -> tuple[str, str] | None:
    """
    Extract (pose_name, variant) from filenames like:
      <prefix>_front_v1_00001_.png  → ("front", "v1")
      <prefix>_3q_left_v2_00001_.png → ("3q_left", "v2")
      <prefix>_01_front_v3_00001_.png → ("front", "v3")
    If prefix is None, matches any leading token (chars up to the first underscore).
    Returns None if the filename doesn't match.
    """
    prefix_re = re.escape(prefix) if prefix else r"[^_]+"
    m = re.match(rf"{prefix_re}_(?:\d+_)?(.+)_v(\d+)_.+\.png", fname)
    if m:
        return m.group(1), f"v{m.group(2)}"
    return None


# ---------------------------------------------------------------------------
# 4. Grade all images
# ---------------------------------------------------------------------------

def grade_images(
    app,
    reference_path: str,
    images_dir: str,
    pattern: str = "*_v*_*.png",
    prefix: str | None = None,
) -> list[dict]:
    """
    Compare every matching image against the reference.
    Returns a list of dicts sorted by similarity (best first).
    """
    print(f"[1/3] Extracting reference embedding from: {reference_path}")
    ref_embedding = get_face_embedding(app, reference_path)
    if ref_embedding is None:
        print("[ERROR] No face detected in reference image!")
        sys.exit(1)

    # Collect image files
    image_files = sorted(Path(images_dir).glob(pattern))
    if not image_files:
        print(f"[ERROR] No images matching '{pattern}' in {images_dir}")
        sys.exit(1)

    print(f"[2/3] Grading {len(image_files)} images against reference...")
    results = []
    no_face = []

    for i, img_path in enumerate(image_files, 1):
        fname = img_path.name
        parsed = parse_filename(fname, prefix)
        pose = parsed[0] if parsed else "unknown"
        variant = parsed[1] if parsed else "?"

        embedding = get_face_embedding(app, str(img_path))
        if embedding is None:
            no_face.append(fname)
            continue

        sim = cosine_similarity(ref_embedding, embedding)
        results.append({
            "filename": fname,
            "path": str(img_path),
            "pose": pose,
            "variant": variant,
            "similarity": round(sim, 6),
        })

        # Progress
        if i % 10 == 0 or i == len(image_files):
            print(f"  Processed {i}/{len(image_files)}...")

    if no_face:
        print(f"\n  [WARN] {len(no_face)} images had no face detected:")
        for f in no_face:
            print(f"    - {f}")

    # Sort by similarity descending (best first)
    results.sort(key=lambda r: r["similarity"], reverse=True)

    print(f"[3/3] Done. {len(results)} images graded, {len(no_face)} no-face.")
    return results


# ---------------------------------------------------------------------------
# 5. Save report
# ---------------------------------------------------------------------------

def save_report(results: list[dict], out_dir: str):
    """Save CSV + JSON report and per-pose ranking."""
    os.makedirs(out_dir, exist_ok=True)

    # --- Full CSV ---
    csv_path = os.path.join(out_dir, "face_similarity_report.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rank", "pose", "variant", "similarity", "filename"])
        for i, r in enumerate(results, 1):
            writer.writerow([i, r["pose"], r["variant"], r["similarity"], r["filename"]])
    print(f"\n  CSV: {csv_path}")

    # --- Full JSON ---
    json_path = os.path.join(out_dir, "face_similarity_report.json")
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  JSON: {json_path}")

    # --- Per-pose best ---
    poses: dict[str, list[dict]] = {}
    for r in results:
        poses.setdefault(r["pose"], []).append(r)

    best_per_pose = {}
    print(f"\n  {'Pose':<15} {'Best Sim':>8}  {'Best Variant':>12}  {'Filename'}")
    print(f"  {'─'*15} {'─'*8}  {'─'*12}  {'─'*40}")
    for pose, items in sorted(poses.items()):
        best = items[0]
        best_per_pose[pose] = best
        print(f"  {pose:<15} {best['similarity']:>8.4f}  {best['variant']:>12}  {best['filename']}")

    # --- Per-pose winners JSON ---
    winners_path = os.path.join(out_dir, "best_per_pose.json")
    with open(winners_path, "w") as f:
        json.dump(best_per_pose, f, indent=2)
    print(f"\n  Per-pose winners: {winners_path}")


# ---------------------------------------------------------------------------
# 5b. Save HTML report
# ---------------------------------------------------------------------------

def _tier(sim: float) -> tuple[str, str]:
    """Return (label, css_class) for a similarity score."""
    if sim >= 0.70: return ("very high", "tier-vhigh")
    if sim >= 0.55: return ("high",      "tier-high")
    if sim >= 0.40: return ("borderline","tier-mid")
    if sim >= 0.25: return ("low",       "tier-low")
    return ("very low", "tier-vlow")


def save_html_report(results: list[dict], reference_path: str, out_dir: str):
    """Render an HTML report with thumbnails, scores, and tier coloring."""
    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, "report.html")

    sims = [r["similarity"] for r in results]
    stats = {
        "count": len(results),
        "best": max(sims),
        "worst": min(sims),
        "mean": float(np.mean(sims)),
        "median": float(np.median(sims)),
    }

    def rel(path: str) -> str:
        try:
            return os.path.relpath(path, out_dir)
        except ValueError:
            return path

    ref_rel = rel(reference_path)

    poses: dict[str, list[dict]] = {}
    for r in results:
        poses.setdefault(r["pose"], []).append(r)

    def card(r: dict, rank: int | None = None) -> str:
        label, cls = _tier(r["similarity"])
        rank_html = f'<span class="rank">#{rank}</span>' if rank else ""
        img_rel = rel(r["path"])
        return f"""
        <figure class="card {cls}">
          <a href="{img_rel}" target="_blank"><img src="{img_rel}" loading="lazy"></a>
          <figcaption>
            {rank_html}
            <span class="sim">{r['similarity']:.4f}</span>
            <span class="tier-label">{label}</span>
            <div class="meta">{r['pose']} · {r['variant']}</div>
            <div class="fname">{r['filename']}</div>
          </figcaption>
        </figure>"""

    ranked_cards = "\n".join(card(r, i + 1) for i, r in enumerate(results))

    pose_sections = []
    for pose, items in sorted(poses.items()):
        items_sorted = sorted(items, key=lambda r: r["similarity"], reverse=True)
        cards = "\n".join(card(r, i + 1) for i, r in enumerate(items_sorted))
        pose_sections.append(f"""
        <section class="pose">
          <h3>{pose} <small>({len(items)} variants · best {items_sorted[0]['similarity']:.4f})</small></h3>
          <div class="grid">{cards}</div>
        </section>""")
    pose_html = "\n".join(pose_sections)

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Face Similarity Report</title>
<style>
  :root {{
    --bg: #1a1a1a; --fg: #eee; --muted: #888; --card-bg: #242424;
    --vhigh:#2ecc71; --high:#7fce6b; --mid:#e6c200; --low:#e67e22; --vlow:#e74c3c;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 24px; font: 14px/1.5 -apple-system, system-ui, sans-serif; background: var(--bg); color: var(--fg); }}
  h1, h2, h3 {{ margin: 0 0 12px; }}
  h1 {{ font-size: 22px; }}
  h2 {{ font-size: 18px; margin-top: 32px; border-bottom: 1px solid #333; padding-bottom: 6px; }}
  h3 {{ font-size: 15px; margin-top: 20px; color: #ccc; }}
  h3 small {{ color: var(--muted); font-weight: normal; }}
  .summary {{ display: flex; gap: 24px; flex-wrap: wrap; background: var(--card-bg); padding: 16px; border-radius: 8px; margin-bottom: 24px; }}
  .summary .stat {{ display: flex; flex-direction: column; }}
  .summary .stat-label {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .summary .stat-value {{ font-size: 18px; font-weight: 600; }}
  .reference {{ display: flex; gap: 16px; align-items: flex-start; background: var(--card-bg); padding: 16px; border-radius: 8px; margin-bottom: 24px; }}
  .reference img {{ width: 180px; height: 180px; object-fit: cover; border-radius: 6px; }}
  .legend {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0 16px; font-size: 12px; }}
  .legend span {{ padding: 4px 8px; border-radius: 4px; color: #000; font-weight: 500; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }}
  .card {{ margin: 0; background: var(--card-bg); border-radius: 8px; overflow: hidden; border-left: 4px solid var(--muted); }}
  .card img {{ width: 100%; aspect-ratio: 1; object-fit: cover; display: block; cursor: zoom-in; }}
  .card figcaption {{ padding: 10px 12px; }}
  .card .rank {{ display: inline-block; background: #333; color: #eee; padding: 1px 6px; border-radius: 3px; font-size: 11px; margin-right: 6px; }}
  .card .sim {{ font-weight: 700; font-size: 16px; }}
  .card .tier-label {{ float: right; font-size: 11px; color: var(--muted); text-transform: uppercase; }}
  .card .meta {{ color: #bbb; margin-top: 4px; }}
  .card .fname {{ color: var(--muted); font-size: 11px; margin-top: 2px; word-break: break-all; }}
  .tier-vhigh {{ border-left-color: var(--vhigh); }}
  .tier-vhigh .sim {{ color: var(--vhigh); }}
  .tier-high  {{ border-left-color: var(--high); }}
  .tier-high  .sim {{ color: var(--high); }}
  .tier-mid   {{ border-left-color: var(--mid); }}
  .tier-mid   .sim {{ color: var(--mid); }}
  .tier-low   {{ border-left-color: var(--low); }}
  .tier-low   .sim {{ color: var(--low); }}
  .tier-vlow  {{ border-left-color: var(--vlow); }}
  .tier-vlow  .sim {{ color: var(--vlow); }}
  .legend .tier-vhigh {{ background: var(--vhigh); }}
  .legend .tier-high  {{ background: var(--high); }}
  .legend .tier-mid   {{ background: var(--mid); }}
  .legend .tier-low   {{ background: var(--low); }}
  .legend .tier-vlow  {{ background: var(--vlow); }}
</style>
</head><body>

<h1>Face Similarity Report</h1>

<div class="reference">
  <a href="{ref_rel}" target="_blank"><img src="{ref_rel}"></a>
  <div>
    <div style="color: var(--muted); font-size: 11px; text-transform: uppercase;">Reference</div>
    <div style="font-size: 16px; font-weight: 600;">{os.path.basename(reference_path)}</div>
    <div style="color: var(--muted); margin-top: 4px; font-size: 12px;">{reference_path}</div>
  </div>
</div>

<div class="summary">
  <div class="stat"><span class="stat-label">Graded</span><span class="stat-value">{stats['count']}</span></div>
  <div class="stat"><span class="stat-label">Best</span><span class="stat-value">{stats['best']:.4f}</span></div>
  <div class="stat"><span class="stat-label">Worst</span><span class="stat-value">{stats['worst']:.4f}</span></div>
  <div class="stat"><span class="stat-label">Mean</span><span class="stat-value">{stats['mean']:.4f}</span></div>
  <div class="stat"><span class="stat-label">Median</span><span class="stat-value">{stats['median']:.4f}</span></div>
</div>

<div class="legend">
  <span class="tier-vhigh">≥ 0.70 very high</span>
  <span class="tier-high">≥ 0.55 high</span>
  <span class="tier-mid">≥ 0.40 borderline</span>
  <span class="tier-low">≥ 0.25 low</span>
  <span class="tier-vlow">&lt; 0.25 very low</span>
</div>

<h2>Ranked (best first)</h2>
<div class="grid">{ranked_cards}</div>

<h2>By pose</h2>
{pose_html}

</body></html>
"""
    with open(html_path, "w") as f:
        f.write(html)
    print(f"  HTML report: {html_path}")


# ---------------------------------------------------------------------------
# 6. Create visual review sheets
# ---------------------------------------------------------------------------

def create_review_sheets(results: list[dict], out_dir: str, thumb_size: int = 512):
    """
    Create visual sheets:
    - Ranked overview: all images sorted by similarity (top 20)
    - Per-pose comparison: variants side-by-side with similarity scores
    """
    os.makedirs(out_dir, exist_ok=True)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 28)
        font_sm = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
        font_xs = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
    except Exception:
        font = ImageFont.load_default()
        font_sm = font
        font_xs = font

    # --- Top 20 ranked sheet (4 columns × 5 rows) ---
    top_n = min(20, len(results))
    cols = 4
    rows = (top_n + cols - 1) // cols
    margin = 10
    label_h = 50

    sheet_w = cols * (thumb_size + margin) + margin
    sheet_h = rows * (thumb_size + label_h + margin) + margin + 60

    sheet = Image.new("RGB", (sheet_w, sheet_h), (30, 30, 30))
    draw = ImageDraw.Draw(sheet)
    draw.text((margin, 10), f"Top {top_n} by Face Similarity (ranked)", fill="white", font=font)

    for i, r in enumerate(results[:top_n]):
        col = i % cols
        row = i // cols
        x = margin + col * (thumb_size + margin)
        y = 60 + row * (thumb_size + label_h + margin)

        # Load and resize
        img = Image.open(r["path"]).convert("RGB")
        img.thumbnail((thumb_size, thumb_size), Image.LANCZOS)
        # Center in cell
        offset_x = x + (thumb_size - img.width) // 2
        offset_y = y
        sheet.paste(img, (offset_x, offset_y))

        # Label: rank, similarity, pose
        draw.text((x + 4, y + thumb_size + 2), f"#{i+1}  sim={r['similarity']:.3f}  {r['pose']} {r['variant']}", fill="white", font=font_xs)

    top_path = os.path.join(out_dir, "ranked_top20.png")
    sheet.save(top_path, quality=95)
    print(f"\n  Ranked sheet: {top_path}")

    # --- Per-pose variant comparison sheets ---
    pose_dir = os.path.join(out_dir, "per_pose_sheets")
    os.makedirs(pose_dir, exist_ok=True)

    poses: dict[str, list[dict]] = {}
    for r in results:
        poses.setdefault(r["pose"], []).append(r)

    for pose, items in sorted(poses.items()):
        n = len(items)
        cols_p = min(n, 4)
        rows_p = (n + cols_p - 1) // cols_p

        sw = cols_p * (thumb_size + margin) + margin
        sh = rows_p * (thumb_size + label_h + margin) + margin + 50

        ps = Image.new("RGB", (sw, sh), (30, 30, 30))
        draw_p = ImageDraw.Draw(ps)
        draw_p.text((margin, 8), f"Pose: {pose}  ({n} variants)", fill="white", font=font)

        # Sort this pose's variants by similarity (best first)
        items.sort(key=lambda r: r["similarity"], reverse=True)

        for i, r in enumerate(items):
            col = i % cols_p
            row = i // cols_p
            x = margin + col * (thumb_size + margin)
            y = 50 + row * (thumb_size + label_h + margin)

            img = Image.open(r["path"]).convert("RGB")
            img.thumbnail((thumb_size, thumb_size), Image.LANCZOS)
            offset_x = x + (thumb_size - img.width) // 2
            ps.paste(img, (offset_x, y))

            # Highlight best with green label
            color = (100, 255, 100) if i == 0 else (200, 200, 200)
            label = f"{'★ ' if i == 0 else ''}{r['variant']}  sim={r['similarity']:.3f}"
            draw_p.text((x + 4, y + thumb_size + 2), label, fill=color, font=font_xs)

        pose_path = os.path.join(pose_dir, f"pose_{pose}.png")
        ps.save(pose_path, quality=95)

    print(f"  Per-pose sheets: {pose_dir}/")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Grade generated character sheet images by face similarity to reference."
    )
    parser.add_argument(
        "--reference", "-r",
        required=True,
        help="Path to the reference face image (e.g., nikki-front.png)",
    )
    parser.add_argument(
        "--images-dir", "-i",
        required=True,
        help="Directory containing generated images to grade",
    )
    parser.add_argument(
        "--out", "-o",
        required=True,
        help="Output directory for reports and review sheets",
    )
    parser.add_argument(
        "--pattern", "-p",
        default=None,
        help="Glob pattern to match generated images (default: <prefix>_*_v*_*.png, or *_v*_*.png if no prefix)",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="Filename prefix to match (e.g., 'nikki'). If omitted, any prefix is accepted.",
    )
    parser.add_argument(
        "--thumb-size",
        type=int,
        default=512,
        help="Thumbnail size for review sheets (default: 512)",
    )
    args = parser.parse_args()

    pattern = args.pattern or (f"{args.prefix}_*_v*_*.png" if args.prefix else "*_v*_*.png")

    print("=" * 60)
    print("Face Similarity Grader")
    print("=" * 60)
    print(f"  Reference:  {args.reference}")
    print(f"  Images dir: {args.images_dir}")
    print(f"  Pattern:    {pattern}")
    print(f"  Prefix:     {args.prefix or '(any)'}")
    print(f"  Output:     {args.out}")
    print()

    # Init
    app = init_face_analyzer()

    # Grade
    results = grade_images(app, args.reference, args.images_dir, pattern, args.prefix)

    if not results:
        print("\nNo results to report.")
        sys.exit(0)

    # Stats
    sims = [r["similarity"] for r in results]
    print(f"\n--- Summary ---")
    print(f"  Images graded:  {len(results)}")
    print(f"  Best similarity: {max(sims):.4f}")
    print(f"  Worst similarity: {min(sims):.4f}")
    print(f"  Mean similarity:  {np.mean(sims):.4f}")
    print(f"  Median similarity: {np.median(sims):.4f}")

    # Save report
    save_report(results, args.out)

    # HTML report
    save_html_report(results, args.reference, args.out)

    # Create visual sheets
    create_review_sheets(results, args.out, thumb_size=args.thumb_size)

    print("\n✓ Done!")


if __name__ == "__main__":
    main()
