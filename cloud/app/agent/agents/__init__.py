"""Agent routing layer.

fc_router does one function-calling call (route_with_fc) over the full tool
catalog and picks the function, mood, and persona for each message.

We used to have a RouterAgent classifier plus per-category specialists, but
dropped them once routing became a single FC call.
"""
