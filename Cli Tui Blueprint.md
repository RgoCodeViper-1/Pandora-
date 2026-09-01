------------------------------------------------------------

# Pandora Terminal Theme

Pandora follows a strict semantic color system.

Colors are not decorative.

Every color has a defined meaning and must remain consistent throughout the application.

Primary Theme

🔴 Pandora Red

Purpose

• Pandora identity
• Primary branding
• Assistant responses
• Titles
• Active selections
• Borders
• Terminal accents
• Final AI responses

Neutral

⚪ White

Purpose

• General information
• Labels
• Metadata
• Logs
• Secondary text
• Tables
• Markdown body
• Help text

Processing

🔵 Blue

Purpose

• Initializing
• Listening
• Thinking
• Loading
• Connecting
• Streaming
• Executing
• Background operations
• Long running tasks

Success

🟢 Green

Purpose

• Completed operations
• Successful execution
• Connected services
• Validation passed
• Ready state

Warning / Error

🟠 Orange

Purpose

• Errors
• Exceptions
• Permission denied
• Failed execution
• Warnings
• Timeouts
• Service unavailable

User Input

🟡 Yellow-Lime

Purpose

• Terminal prompt
• User commands
• Editable text
• Cursor line
• Interactive selections

Never assign multiple meanings to the same color.

Maintain semantic consistency across every screen.

------------------------------------------------------------

# Terminal Theme Variables

Define a centralized theme.

Never hardcode colors throughout the application.

The terminal renderer should expose semantic colors such as

theme.primary

theme.text

theme.processing

theme.success

theme.error

theme.input

Every component should consume the semantic theme instead of literal colors.

------------------------------------------------------------

# Backend Integration Philosophy

The frontend is NOT responsible for business logic.

It is a reactive presentation layer.

The backend owns all application state.

React renders that state.

The communication flow is:

Python
        │
        ▼
Rust Core Engine
        │
        ▼
Tauri Commands / Events
        │
        ▼
Frontend Services
        │
        ▼
React Components

Every backend event should update frontend state.

React components should never poll continuously when an event-driven update is available.

------------------------------------------------------------

# Frontend Event Layer

Create a dedicated communication layer.

Example responsibilities include:

• subscribing to backend events

• invoking backend commands

• translating backend payloads into UI state

• exposing typed APIs to React components

Components should never communicate directly with Tauri.

Instead use service abstractions.

Example

Conversation Component

↓

Conversation Service

↓

Tauri Event Layer

↓

Rust

------------------------------------------------------------

# Component Wiring

Every visual region should receive data through typed properties or state.

Never tightly couple UI components together.

Example

Backend

↓

State Store

↓

Conversation Component

↓

Markdown Renderer

↓

Terminal Output

Another example

Backend

↓

Telemetry Service

↓

Metrics Panel

↓

CPU

RAM

GPU

Network

Each component owns only its own presentation.

------------------------------------------------------------

# Markdown Rendering

Markdown is used only for rendering content.

Markdown is NOT responsible for layout.

Markdown is NOT responsible for colors.

Markdown is NOT responsible for positioning.

Markdown should inherit the Pandora theme.

Support

• headings

• lists

• code blocks

• tables

• block quotes

• inline formatting

without changing the surrounding terminal layout.

------------------------------------------------------------

# Layout System

The interface should use Yoga/Flexbox as the primary layout engine.

Every major UI region should be a Flex container.

Examples

Header

Conversation

Telemetry

Task Queue

Notifications

Input

Status Bar

Avoid absolute positioning unless it is required for overlays or animations.

------------------------------------------------------------

# Animation System

The terminal should never feel static.

Animations should communicate state.

Examples

Processing

Loading

Typing

Streaming

Progress

Expanding sections

Collapsing sections

Cursor movement

Status transitions

Animations should remain lightweight and deterministic.

Avoid excessive motion.

------------------------------------------------------------

# Rich Terminal Experience

The interface should resemble a professional operating console rather than a traditional CLI.

Prefer

• Chalk

• Gradient text where appropriate

• Unicode symbols

• Terminal animations

• Markdown rendering

• Responsive layouts

• Streaming output

• Live panels

• Dynamic resizing

over plain console.log output.

------------------------------------------------------------

# Future Compatibility

Every UI component should remain independent of the backend implementation.

Whether the backend changes from

Python

to

Rust

or

Local AI

or

Remote AI

the frontend should require minimal modification.

Maintain strict separation between

Presentation

Application Logic

System Logic

AI Logic

Communication Layer

Rendering Layer

This separation is a fundamental architectural requirement of Pandora.