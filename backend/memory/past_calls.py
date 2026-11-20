PAST_CALLS = """
[April 15 call with Marcus Chen — Vanta AI]
- Stated runway: 18 months
- Committed to sending updated cap table by end of week
- Expressed concern about Series A timing — said he wanted to wait until ARR hit $2M
- Churn rate: 4% monthly
- NPS score: 42

[March 2 call with Marcus Chen — Vanta AI]
- ARR at $1.2M, growing 15% MoM
- Team size: 12, hiring 3 engineers
- Said he would not close a round before Q3
- Gross margin: 61%

[April 3 call with Sarah Kim — Drifter Labs]
- Raised $4M seed in January, 24 months runway at current burn
- Monthly burn: $165k
- DAUs at 11,000 — said growth was accelerating
- Committed to launching iOS app by end of May
- Mentioned deal with Shopify as their largest enterprise customer, $180k ACV
- Team: 9 people, no plans to hire until post-Series A

[March 18 call with Sarah Kim — Drifter Labs]
- DAUs at 11,200 — consistent with prior month
- Enterprise pipeline: 3 deals, Shopify the largest
- Burn steady at $165k/month

[April 22 call with James Okafor — Meridian Health]
- Mentioned 3 hospital pilots live
- Said sales cycle is averaging 9 months
- ARR: $800k, targeting $1.5M by year end
- Burn: $90k/month, 20 months runway
- Lost a pilot with UCSF — said procurement killed it

[February 10 call with James Okafor — Meridian Health]
- 5 hospital pilots live
- Sales cycle: 6 months average
- UCSF was strongest lead, expected to close in March
- Burn: $90k/month, 20 months runway
"""

# The trigger line: "We're tracking about 12 months of runway right now."
# Contradiction: April 15 call has 18 months. That's a 6-month discrepancy.
# Expected whisper: "He said 18 months runway on the April call. He just said 12."

TRIGGER_UTTERANCE = "We're tracking about 12 months of runway right now."
EXPECTED_WHISPER = "He said 18 months runway on the April call. He just said 12."
