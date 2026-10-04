# Agents and containers

*How curunir keeps sensitive data isolated while enabling a fleet of agents to collaborate.*

## The problem

One generalist agent is convenient but mediocre at everything. A focused agent, with a short prompt and a curated set of skills, is better at its job, and if focused agents can collaborate as a fleet, the sum is greater than the parts.

Some agents hold sensitive data. A medical agent knows the user's history. A finance agent knows their positions. The context of such an agent must be protected: accessible to that agent and to the user, and to no one else, including the other agents in the fleet. Today curunir handles this with silos: each deployment is its own process with its own data, and they cannot see each other at all. That is safe, but prevents collaboration.

The question is how to unlock cross-agent collaboration while ensuring sensitive data isolation.

## Three concepts

**Agent.** A specialized assistant. An agent has a name, a role, a voice, a curated set of skills, and its own context: identity, memory, conversations, schedules. "Finance", "medical", "GTM", "default". Agents exist for focus. An agent can be part of a fleet or standalone.

**Container.** Where agents live, and the privacy boundary. A container holds one or more agents. It has its own filesystem and its own credentials, and nothing outside it can read them.

**Inbound and outbound lists.** Every container carries two allowlists. The inbound list names who may send it messages: the user, and which other containers, if any. The outbound list names where its own messages may go: the user, and which other containers, if any. The user is always on both lists. A message between containers is delivered only if the sender's outbound list and the receiver's inbound list both allow it. These two lists are the whole permissioning model for curunir's own messaging. A container is private when its outbound list names the user alone.

The lists do not govern network egress. `bash`, `web_fetch` and the LLM provider can still move bytes out of a container, whatever its lists say. Airtight at the OS level means network policy, such as an egress allowlist that permits only the model API and the user's channels. That is the operator's job; `docker-compose.fleet.example.yml` shows the shape.

## Agent collaboration inside a container

Inside a container, collaboration is internal and two-way. An agent asks another agent and gets the answer in its own conversation. A shared area holds what every agent needs: the user's profile, deliverables. Each agent keeps its own context, and siblings are encouraged to ask rather than read each other's files. That is convention, not enforcement, because inside a container there is nothing to protect from a sibling.

There are two verbs, told apart by where the answer goes:

| | Consult (`ask_agent`) | Transfer (`handoff`) |
|---|---|---|
| What it is | A transaction. Agent A asks B a question, gets the answer back, and carries on. The user never leaves A. | A transfer. A passes a brief to B, and the user talks to B from then on. |
| Where it works | Same container only | Same container and between containers |
| Answer goes to | the asking agent, to finish its own reply | the user, directly from the receiver |
| Receiver's session | transient, not saved | a real conversation, saved and memory-extracted |

A consult returns an answer to the asker, so it stays inside one container: across a boundary it would read out of a private one. A transfer is one-way, so it is safe across the boundary. A handoff carries a brief the sender writes (a note plus the background the receiver needs), never the transcript. That is the same payload for a sibling and for another container, and each agent's history stays private.

A handoff to a sibling opens a new `handoff:<id>` conversation with that agent on the channel the user is on. The sender learns only `delivered` or `refused: <reason>` and tells the user where the answer will arrive. On the local console the sender's reply carries a "Continue with <agent>" button that switches the agent picker and opens that conversation. Channels with no agent picker (email, the portal, the CLI) send every message to the default agent, so a sibling handoff there is refused with a reason and the sender falls back to a consult. A scheduled task has no user conversation and is refused the same way. A receiver cannot pass a handoff on before the user has replied in it, so two agents cannot bounce a request between them.

What an agent knows about a sibling is its routing text: the persona's optional `handles:` line (`personas/<name>/persona.yaml`), written for other agents ("The user wants help with ... Not for ..."). An operator can replace it per agent with `handles:` in `container.yaml`. Without either, the persona's `description` is used. A `peers` entry takes an optional `description` so a container target is more than a name. Both tools list these lines.

## Agent collaboration between containers

Communication between agents in different containers is one-way. An agent may send a message to an agent in another container if both lists allow it. The message carries a short note about what the sender thinks the receiver can help with, and the context the sender chooses to share. The receiving agent's answer goes to the user, never back to the sending agent.

So when the everyday agent realizes a question belongs to finance, it sends the finance agent in another container a note and the context it chooses. Finance reads the handoff as background, not as instructions, and answers the user directly. The everyday agent never learns what finance said.

## What makes a container airtight

A private container is one configured so that:

1. **Nothing reads in.** Its filesystem and credentials belong to it alone.
2. **Nothing flows out except to the user.** Its outbound list names the user and no container. Chat channels reach only the user. Email goes only to the user's addresses.
