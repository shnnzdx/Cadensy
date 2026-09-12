# Cadensy Project Description

Cadensy is an AI-powered coordination system for group travel. It helps groups turn different preferences, budgets, constraints, and planning rhythms into one shared itinerary that everyone can agree on.

Unlike a simple itinerary generator, Cadensy focuses on the decision process behind group planning. It keeps trip facts, member preferences, constraints, comments, and proposed changes connected to the current plan, so the group can revise the itinerary without restarting the whole planning conversation.

The system combines a React trip workspace, a FastAPI backend, PostgreSQL persistence, provider-backed place data, and AI-assisted planning/chat flows. AI helps explain, generate, and repair plans, while deterministic backend rules decide whether a proposed change should be applied directly, sent to a vote, reopened for discussion, or confirmed by affected members.

## Short Description

Cadensy is an AI-powered group travel coordination system that helps travelers build, discuss, and adapt one shared trip plan.

## Demo Pitch

Cadensy turns group travel planning from scattered discussion into a shared decision workflow. Members can express preferences and constraints, the system builds a living itinerary around them, and AI-assisted plan changes stay grounded in backend validation before the group approves them.

## AI Safety Notes

Cadensy treats AI as an assistant, not the final authority. The model can suggest and explain plan changes, but backend services validate candidate places, protect private constraints, classify decision paths, and require user approval before the current plan changes.

## 中文简介

Cadensy 是一款面向团体旅行的 AI 协调系统，帮助不同偏好、预算、限制和旅行节奏的成员共同制定一个可以持续调整、让所有人都能接受的共享行程。
