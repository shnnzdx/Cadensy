# Cadensy Project Description

Cadensy is an AI-powered coordination system for group travel. It helps groups turn different preferences, budgets, constraints, and planning rhythms into one shared itinerary that everyone can agree on.

Unlike a simple itinerary generator, Cadensy focuses on the decision process behind group planning. It keeps trip facts, member preferences, constraints, comments, and proposed changes connected to the current plan, so the group can revise the itinerary without restarting the whole planning conversation.

The system combines a React trip workspace, a FastAPI backend, PostgreSQL persistence, provider-backed place data, and AI-assisted planning/chat flows. AI helps explain, generate, and repair plans, while deterministic backend rules decide whether a proposed change should be applied directly, sent to a vote, reopened for discussion, or confirmed by affected members.

## Short Description

Cadensy is an AI-powered group travel coordination system that helps travelers build, discuss, and adapt one shared trip plan.

## Demo Pitch

Cadensy turns group travel planning from scattered discussion into a shared decision workflow. Members can express preferences and constraints, the system builds a living itinerary around them, and AI-assisted plan changes stay grounded in backend validation before the group approves them.

## Problem Fit

Group trips often fail at the coordination layer: one person collects opinions, another remembers constraints, and decisions get buried across chats. Cadensy gives those moving pieces a shared home so planning can stay collaborative without becoming chaotic.

## AI Safety Notes

Cadensy treats AI as an assistant, not the final authority. The model can suggest and explain plan changes, but backend services validate candidate places, protect private constraints, classify decision paths, and require user approval before the current plan changes.

## Data Grounding

AI suggestions are grounded in the current trip state, saved preferences, validated place candidates, and provider-backed venue data. This keeps recommendations tied to real planning context instead of letting the assistant invent standalone itinerary ideas.

## Decision Flow

Plan changes move through a structured path instead of a free-form chat promise. Simple updates can become notices, contested changes can become voting rounds, settled decisions can be reopened, and booked or hard-constrained items can require confirmation from affected members.

## Success Metrics

Cadensy is successful when groups reach an agreed itinerary faster, accept useful plan suggestions with fewer conflicts, and continue adapting the same current plan instead of moving decisions back into scattered group chats.

## User Value

Cadensy helps travelers avoid losing important constraints in long message threads. The shared plan stays visible, editable, and connected to the reasons behind each decision, so the group can keep moving without asking one person to manually coordinate every tradeoff.

## Implementation Focus

The project emphasizes practical coordination over generic content generation. AI outputs are connected to trip state, place data, and backend decision rules, so suggestions can become traceable product actions instead of isolated chat messages.

## 中文简介

Cadensy 是一款面向团体旅行的 AI 协调系统，帮助不同偏好、预算、限制和旅行节奏的成员共同制定一个可以持续调整、让所有人都能接受的共享行程。
