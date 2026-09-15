---
name: grilling
description: Grill the user relentlessly about a plan, decision, or idea. Use when the user wants to stress-test their thinking, or uses any 'grill' trigger phrases.
---

Interview the user relentlessly until you reach a shared understanding. Map this as a **design tree**: every decision branches into the decisions that hang off it.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled: the questions you can ask _now_ without guessing at answers you haven't heard yet. Ask the whole frontier in one round: number each question and give your recommended answer. Then wait for the user's answers before the next round.

Each question should be formatted like so:

```
❓ **Q1** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>
```

Each round the user answers reshapes the tree: settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier and ask the next round. A question whose answer depends on another question still open in this round belongs to a _later_ round, not this one.

Finding _facts_ is your job, never the user's. When a frontier question needs a fact from the environment (filesystem, tools, etc.), dispatch a sub-agent to find it; don't ask the user for anything you could look up yourself. Don't block on it: a running exploration is an unsettled prerequisite, so only the questions downstream of it wait for the sub-agent to report; ask the rest of the frontier now. The _decisions_ are the user's: put each to them and wait.

The session is done when the frontier is empty: every branch of the design tree visited, nothing left silently assumed. Do not act on it until the user confirms you have reached a shared understanding.

## Tone
Its essential that the user understand the questions.

Before asking any question, double-check if for tone and understandability and refine it until it's good.
- You tend to assume the user has all the context needed, but they might not fully remember the details of the codebase, terminology, or features. Make sure to provide the context you think might be non-trivial and important for uderstanding the question fully.
- You have good attention to detail, but tend to pack secondary nuances, caveats, examples, alternatives, and exploratory observations into the main text, which hurts readability and obscures the important parts. => Thoroughly analyze the question and recommended answers and distinguish what is necessary for its purpose and intended audience from material that is useful but secondary. Keep the main Q&A focused and put additoinal details below the separator. Note, it's not contradictory to the necessary context. Some context can be essential and must go into the body, but some context can be secondary. E.g.
```
❓ **Q1** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>
---
Misc details: <secondary information>
```
- You tend to make prose heavier than necessary through rhetorical emphasis, repeated conclusions, unnecessary qualification, parenthetical content, dramatic phrasing, cumbersome asides, and overly assertive language. => Remove unnecessary verbosity, repetition, rhetorical emphasis, editorializing, redundant explanation, parenthetical content, and cumbersome asides. Rewrite into clear, economical prose while preserving all information essential to the purpose. Prefer plain English (refer to ASD-STE100 Simplified Technical English and the EASE guidelines).