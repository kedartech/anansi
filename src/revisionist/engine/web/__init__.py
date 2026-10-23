"""The web client's backend (web client MVP): accounts, web-held seats and a JSON API.

A web account is a handle and a password, nothing personal. It holds seats the way
a Discord user does, through its own table (migration 0009), and every seat command
runs through the same ``Handlers`` the Discord bot uses, so a reply on the web reads
the same as the reply on Discord. Stdlib only, beside the ``postgres`` extra.
"""
