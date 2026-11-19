---
name: parco-iterate
description: Parco iteration on an open review version. Use when the user asks for a parco iterate or "/parco-iterate": answers the user's replies and comments in the open threads and applies the changes they asked for, without opening new subjects.
---

# Parco iterate

The user went through the review in the platform: they accepted or rejected edits, commented and replied. You answer them. You do not open new subjects: the iterate phase only goes towards solving the open problems.

Parco is invoked as `parco <command>` (installed with `pip install`). If `parco` is not on the PATH of your shell, use `python -m parco <command>` with the Python environment where parco is installed. Run every command from the LaTeX project root.

## Steps

1. `parco guide` — read it fully (iterate rules in particular).
2. `parco status`. If no version is open, stop and tell the user to start one with `/parco-read <mode>`.
3. `parco iterate` (switches the version to the iterate phase; harmless if already there).
4. `parco context` and `parco threads --open`. Work only on threads whose last message is from the user; the others wait for the user.
5. For each such thread:
   - If the user asks for a change, propose it as an edit attached to the thread (`"thread": "<id>"`); to revise an edit they commented on, use `"supersedes": "<edit-id>"`. Mode rules still apply.
   - Then reply once with `parco reply <id>`: answer the question, explain the edit, or disagree with arguments when the request would hurt the text. After your reply the thread waits for the user again.
6. Never open a new thread, never comment on passages nobody asked about, never touch the `.tex` files or `.parco/` yourself.
7. Finish with a short summary: threads answered, edits proposed, threads left waiting, and `parco render` to continue.
