# System-Initiated Agentic Loops — Design Notes

## Problem Statement

Some use cases require Curunir to act on its own initiative, without a user message as the trigger. Examples:

- **Morning brief**: Every morning, send the user a summary of what's relevant — calendar, tasks, recent activity, weather, etc.
- **Proactive suggestions**: Periodically reflect on how to help the user and propose something useful.
- **Scheduled maintenance**: Run health checks, clean up old files, update summaries.

## The Problem with the Current Architecture

Today, all agentic loops are triggered by a message arriving in the incoming queue. The flow is:

```
user message → incoming queue → agentic loop → tool calls → response
```

System-initiated loops don't have a user message to kick them off. The agent needs to start a loop from a **system prompt** rather than a user message. This is a fundamentally different entry point.

## Key Questions

1. **Triggering**: What initiates the loop? A cron job? An internal scheduler? A separate "initiative" process that watches for conditions?
2. **Prompt source**: Where does the system prompt come from? Static config? A prompt template with dynamic context injected?
3. **Output routing**: Where does the result go? The user's message queue? A notification channel? A file?
4. **Guard rails**: How do we prevent runaway loops or unwanted actions when there's no user in the loop to approve tool calls?
5. **Context**: How does the system-initiated loop get the context it needs (user preferences, current state, recent history) without a user message providing it?
