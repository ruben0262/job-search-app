"""The legal pages: the version a person agrees to at sign-up. Who receives a person's data and what a
device keeps are in `libs.legal.disclosure`; the pages themselves in `libs.legal.documents`."""

# The version of the legal pages — the Terms of Service and the Privacy, Cookie and Data Use policies — that
# the sign-up box agrees to (`services/api/auth_routes.py`), recorded on the account with the moment it was
# given (`processing_consent_*`). Agreed once, at sign-up, and never asked again inside the site (owner,
# 2026-11-18: "that's something users accept on sign up and that's it"): changed terms reach existing accounts
# by email, 30 days ahead (Terms 19). Date-stamped, so a stored agreement says when it was given and to what.
# - 2026-10-28: Google and OpenAI added; Anthropic's purpose documents only.
# - 2026-11-01: the site's one proxy. 2026-11-11: Stripe.
# - 2026-11-12: the cookies, agreed to at sign-up with the rest (owner: "a box to confirm the privacy policy
#   and cookies of the site before signing up").
# - 2026-11-18: the four pages written as a set, and the box agrees to the Terms. Cloudflare named, which
#   carries every visit. The proxy dropped: since 2026-11-04 it reads LinkedIn by country, with nothing of
#   anybody's search. CVs and letters only on the person's own AI account (2026-11-09).
# - 2026-11-19: a Discord account may be linked: Discord and RoleLogic named, and Stripe receives its ID.
LEGAL_VERSION = "2026-11-19"

# Each page's address on the site (`/terms` …) and its title. Here, with nothing imported, so the docgen
# worker can know the public pages (`libs.pages`) without the sign-in code `documents` needs (argon2).
DOCUMENTS = {
    "terms": "Terms of Service",
    "privacy": "Privacy Policy",
    "cookies": "Cookie Policy",
    "data": "Data Use Policy",
}
