# Newlang _(name TBD)_

A systems programming language designed for simplicity, low-level control, and seamless dual-mode execution.

## Overview

Newlang is a systems programming language built around a core idea: code should be able to run compiled _and_ interpreted, with full interoperability between the two modes. A compiled binary can call into an embedded interpreter, and interpreted code can call compiled code — no special glue required.

The language is simple by design, yet expressive enough for low-level work. Writing most of an OS kernel in it (short of the parts that require assembly) should be entirely feasible.

## Goals

- **Dual-mode execution** — first-class support for both compilation and interpretation, with seamless interop between modes.
- **Embeddable interpreter** — the interpreter is small enough to run on systems with only a few megabytes of RAM with room to spare, making it practical to embed inside a compiled application and interact with the surrounding compiled code at runtime.
- **REPL support** — the interpreted mode should make it easy to provide an interactive REPL.
- **Low resource footprint** — both the compiler and interpreter are designed to run on systems with only a few megabytes of RAM.
- **Low-level capability** — suitable for systems programming, including OS kernel development (everything short of raw assembly).
- **Self-hosting** — the long-term goal is for the compiler and interpreter to be written in Newlang itself.
- **Simple and approachable** — the language should be easy to learn and use, without sacrificing power.

## Target Platforms

The primary targets are **32-bit systems**, with full support for **64-bit systems** as well (enabling development and use on modern hardware).

The language reflects the native CPU word size of the target platform. It assumes characteristics common to modern CPUs — for example, two's complement integer representation.

## Status

Early design and exploration phase.
