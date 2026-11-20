DECOYS = [
    "Yeah, I think we're in a good spot heading into Q3.",
    "Let me think about that and get back to you.",
    "We've been heads-down on product for the last few weeks.",
    "Marcus is great. I really enjoyed our last conversation.",
    "We're hiring a couple of engineers right now.",
    "I'll send over the deck after this call.",
    "Honestly, the market's been pretty tough lately.",
    "Can you walk me through your go-to-market again?",
    "We're growing pretty quickly, maybe 10 to 15 percent month over month.",
    "Series A is definitely on our radar for later this year.",
    "We have about 12 people on the team right now.",
    "I think we can get to a million in ARR by end of year.",
    "Our burn rate has come down a lot since last quarter.",
    "I'm not really worried about the competitive landscape right now.",
    "We'd love to keep this conversation going after the holidays.",
    "The cap table is clean — only two investors so far.",
    "Our runway is comfortable. I'm not stressed about it.",
    "Let's plan to reconnect in a few weeks.",
    "Things have been moving fast on the product side.",
    "We're still figuring out the exact timing for the raise.",
]

# (trigger_utterance, expected_whisper_content)
TRIGGERS = [
    (
        "We're tracking about 12 months of runway right now.",
        "He said 18 months runway on the April call. He just said 12.",
    ),
]
