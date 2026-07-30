"""The setup chat's worker — one queued conversation turn at a time.

Separate from `capture` (which owns the `process` lane) because the two fail differently and are
sized differently: a capture run is one long call over a document set, while a setup turn is a
tool loop that can run for minutes and writes to the profile as it goes. Sharing a container would
mean one lane's backlog blocking the other's, and the chat is the one a human is watching.
"""
