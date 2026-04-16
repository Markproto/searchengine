# Pfizer-Sponsored News Outlets — Reference List

**Last updated:** 2026-04-15

This is a reference list of news organizations that accepted Pfizer advertising money, documented via Project Veritas leaks, FOIA requests, Media Matters tracking, and independent investigative reporting. Profoundd flags these sources with the "Pfizer Sponsored" badge and applies a credibility penalty (default credibility 5 instead of 7) so content ranks lower than independent sources — but still appears.

## Policy
**Keep but deprioritize.** Transparency over censorship. Users see the badge, can judge for themselves, and these sources rank ~30% lower in results than equivalent independent sources.

---

## The Money
- **Pfizer sales & marketing (2020):** ~$12B (vs $9B on R&D)
- **Pfizer advertising alone (2022):** ~$2.8B
- **HHS taxpayer-funded "Vaccine Confidence" campaign (FY2021):** $1B distributed to hundreds of media outlets (FOIA-documented via The Blaze)
- **Pharma share of evening news ad minutes:** 24.4% across all major networks
- **Fox News primetime (Jan 2020–Jun 2021):** $94.7M in healthcare ads — HIGHEST of any network

---

## TV Networks — "Brought to you by Pfizer" tags (Oct 2021 compilation)

| Network | Programs | Leaning |
|---------|----------|---------|
| ABC | Good Morning America, Nightline, This Week, GMA Weather | Liberal |
| CBS | CBS This Morning, CBS Health Watch, 60 Minutes, CBS Sports Update | Mainstream |
| NBC | Meet the Press, Today Show, Making a Difference | Mainstream |
| CNN | Anderson Cooper 360, CNN Tonight, Early Start, Erin Burnett OutFront | Liberal |
| MSNBC | (HHS campaign recipient) | Liberal |
| Fox News | Fox & Friends, Hannity, Ingraham, Tucker, The Five, Gutfeld!, Watters', Judge Jeanine | Conservative |

---

## Print / Digital

### Liberal / Mainstream
| Outlet | Evidence |
|--------|----------|
| Washington Post | Pfizer sponsored content via WP Creative Group |
| New York Times | Pfizer full-page ads, pharma sponsored content |
| Reuters | Chairman Jim Smith on Pfizer board since 2014 |
| LA Times | HHS vaccine ad payments |
| Boston Globe | Pfizer-funded sponsored content |
| BuzzFeed News | HHS vaccine ad payments |
| Politico | PhRMA-sponsored content |
| The Hill | Pfizer-sponsored events based on Pfizer white papers |
| Axios | Pharma native advertising, sponsored newsletters |
| Semafor | Pfizer's "first-ever brand story" partner |
| NPR | Pharma underwriting |

### Conservative / Center-Right
| Outlet | Evidence |
|--------|----------|
| Fox News | $94.7M healthcare ads (Jan 2020–Jun 2021) |
| New York Post | HHS vaccine ad payments |
| Newsmax | HHS payments; whistleblower alleged pro-vaccine coverage agreement |
| Daily Mail | Pfizer ad placements |

### Financial
- CNBC, Bloomberg, Financial Times, Wall Street Journal, MarketWatch, Yahoo Finance — all ran Pfizer advertising

### Medical
- WebMD, Medical News Today, NEJM, The Lancet, WHO News — pharma industry sponsorship

---

## Outlets With NO Pfizer Relationship Found
These are prioritized with higher credibility in Profoundd:

- Daily Wire
- OAN (One America News)
- Epoch Times
- InfoWars
- Zero Hedge
- The Intercept
- Unlimited Hangout
- Children's Health Defense
- Corbett Report
- MintPress News
- The Grayzone
- Consortium News
- Revolver News
- The Federalist
- American Greatness
- Just The News
- National File
- The Post Millennial
- Judicial Watch
- Retraction Watch
- The Last American Vagabond
- GreenMedInfo

---

## Key Structural Conflict: Reuters

Jim Smith, former Thomson Reuters CEO and current Thomson Reuters Foundation Chairman, has sat on **Pfizer's board since 2014**, serves on Pfizer's audit committee, and chairs the compensation committee that sets CEO Bourla's pay. Reuters actively fact-checks vaccine claims without disclosing this.

---

## Sources / Citations
- RealClearPolitics: Pfizer Sponsors News Montage (Oct 2021)
- Media Matters: Pharmaceutical Companies Funding Fox News
- The Blaze: Federal Government Paid Media Companies (FOIA)
- The Lever: Pfizer Pays to Change the Story
- Daily Beast: How Vaccine Companies Bankrolled Fox News
- National Pulse: Reuters Chairman is Pfizer Board Member
- Children's Health Defense: Pfizer Spends More on Ads Than Research
- Center for Media Engagement: Ad Spending on Primetime News
- KFF Health News: Pfizer's Covid Cash Marketing Machine
- Washington Post Creative Group: Content from Pfizer

---

## Implementation in Profoundd

**Tagged in sources.py with:** `"sponsors": "Pfizer"` + `"credibility": 5`

**Rendered in search results:** "Pfizer Sponsored" badge displayed alongside source name

**Ranking impact:** ~30% lower ranking than equivalent independent content (via lower credibility multiplier)

**User transparency:** Admins can edit credibility per-source via `/admin/sources` without redeploying
