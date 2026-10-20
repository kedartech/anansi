"""Discord transport (build plan Layer 9a; design §15).

``rest`` and ``tables`` are stdlib only, so the one-shot scheduler can deliver
by DM without the gateway bot or the ``discord`` extra. ``bot`` is the gateway
process and the only module that imports ``discord.py``.
"""
