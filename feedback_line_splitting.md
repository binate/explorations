---
name: Line splitting preference
description: When shortening long lines, prefer breaking the line over introducing new variables
type: feedback
---

When fixing long lines, prefer "brute force" line splitting (breaking the expression across lines) over extracting subexpressions into new local variables. Only introduce a new variable if it would be used more than once.

**Why:** Adding variables for single-use subexpressions adds noise and changes the code structure unnecessarily.

**How to apply:** When a line exceeds the length limit, find a natural break point (after a comma, before an argument) and wrap. Only extract to a variable if the same subexpression repeats in nearby lines.
