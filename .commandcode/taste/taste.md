# Taste (Continuously Learned by [CommandCode][cmd])

[cmd]: https://commandcode.ai/

# workflow
- Directly edit files rather than providing manual instructions when the user asks to make changes. Confidence: 0.75

# prompt-engineering
- Don't embed full conversation transcripts or specific caller examples (e.g., "B.Tech ECE") in system prompts — keep examples abstract and generic to handle diverse callers and avoid bloating prompt size. Confidence: 0.75
- For outbound call system prompts: instruct the agent to proactively explain business details (features, pricing, placement, benefits) without asking the caller for permission to share information. Confidence: 0.70
- Use a median response length (3-4 sentences) for outbound call system prompts — not as short as 1-2 lines, not as long as 5-6 lines — to keep callers engaged without overwhelming them. Confidence: 0.70

