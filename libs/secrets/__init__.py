"""Envelope encryption for per-user credentials (KEK/DEK).

Every user brings their own Claude credential, Telegram bot token, and proxy credential. Those are
the highest-consequence rows in the database: a leak spends someone else's money and, for the
proxy, breaches a third party's terms.

Envelope encryption rather than a single key: a per-user data key (DEK) encrypts the secret, and a
key-encryption key (KEK) held outside the database encrypts the DEK. The property that buys is
that a database dump — or a leaked backup — is not sufficient to read any credential.

**The KEK is backed up separately from the database** (OPERATIONS_PLAN.md §7). Backing both up to
the same place fails silently in both directions: together, the backup is as good as plaintext;
apart-but-forgotten, the restore produces a database of unreadable secrets. Verify both halves in
the restore drill, before the first invited user.

Isolation is tested, not assumed — TEST_STRATEGY.md T4 sits in the same commit as this module.
"""
