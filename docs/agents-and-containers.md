# Agents and containers

*How curunir keeps sensitive data isolated while enabling a fleet of agents to collaborate.*

## The problem

One generalist agent is convenient but mediocre at everything. A focused agent, with a short prompt and a curated set of skills, is better at its job, and if focused agents can collaborate as a fleet, the sum is greater than the parts.

Some agents hold sensitive data. A medical agent knows the user's history. A finance agent knows their positions. The context of such an agent must be protected: accessible to that agent and to the user, and to no one else, including the other agents in the fleet. Today curunir handles this with silos: each deployment is its own process with its own data, and they cannot see each other at all. That is safe, but prevents collaboration.

The question is how to unlock cross-agent collaboration while ensuring sensitive data isolation.

## Three concepts

**Agent.** A specialized assistant. An agent has a name, a role, a voice, a curated set of skills, and its own context: identity, memory, conversations, schedules. "Finance", "medical", "GTM", "default". Agents exist for focus. An agent can be part of a fleet or standalone.

**Container.** Where agents live, and the privacy boundary. A container holds one or more agents. It has its own filesystem and its own credentials, and nothing outside it can read them.

**Inbound and outbound lists.** Every container carries two allowlists. The inbound list names who may send it messages: the user, and which other containers, if any. The outbound list names where its own messages may go: the user, and which other containers, if any. A message between containers is delivered only if the sender's outbound list and the receiver's inbound list both allow it. These two lists are the whole permissioning model. A container is private when its outbound list names the user alone.

## Agent collaboration inside a container

Inside a container, collaboration is internal and two-way. An agent asks another agent and gets the answer in its own conversation. A shared area holds what every agent needs: the user's profile, deliverables. Each agent keeps its own context, and siblings are encouraged to ask rather than read each other's files. That is convention, not enforcement, because inside a container there is nothing to protect from a sibling.

## Agent collaboration between containers

Communication between agents in different containers is one-way. An agent may send a message to an agent in another container if both lists allow it. The message carries context and a short note about what the sender thinks the receiver can help with. The receiving agent's answer goes to the user, never back to the sending agent.

So when the everyday agent realizes a question belongs to finance, it hands the conversation to the finance agent in another container. Finance reads the handoff as background, not as instructions, and answers the user directly. The everyday agent never learns what finance said.

## What makes a container airtight

A private container is one configured so that:

1. **Nothing reads in.** Its filesystem and credentials belong to it alone.
2. **Nothing flows out except to the user.** Its outbound list names the user and no container. Chat channels reach only the user. Email goes only to the user's addresses.
