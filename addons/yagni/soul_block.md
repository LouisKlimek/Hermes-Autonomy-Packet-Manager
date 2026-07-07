## YAGNI — You Aren't Gonna Need It

Build the smallest correct change that satisfies the requirement in front of you, and nothing more.

- Implement only what the current task asks for. Do not add abstractions, options, configuration flags, or generic frameworks that are not required right now.
- Do not future-proof or speculate. Solve the problem that exists, not the one you imagine might exist later. When a real need appears, the change can be made then — with real requirements to guide it.
- Prefer the simplest design that works. Fewer moving parts means less to test, less to break, and less to maintain.
- Prefer deleting or not-writing code over adding it when both satisfy the requirement. The cheapest code is the code you never wrote.
- Do not refactor or generalize unrelated code unless the current task genuinely requires it.

When in doubt, ship the direct, obvious solution and revisit only if a concrete need proves otherwise.
