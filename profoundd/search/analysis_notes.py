"""
Editorial context notes injected into AI article analysis.
These provide statistical background on pollster accuracy, known anomalies,
historical mispolls, and election irregularities. The AI uses these as
factual context — we present statistics, not conclusions.
"""

# Notes are keyed by topic keywords that trigger inclusion
ANALYSIS_NOTES = {
    # --- Pollster Accuracy Context ---
    "polling": """
STATISTICAL CONTEXT — POLLING ACCURACY:
- In 2016, national polls missed Trump's margin by ~2 points on average. State polls
  in WI, MI, PA were off by 5-7 points, failing to detect late-deciding voters.
- In 2020, polls overestimated Biden's lead nationally by ~3.9 points (FiveThirtyEight),
  the largest polling miss since 1980. State-level errors were even larger in some cases
  (WI off by 8.4 points, OH off by 12 points).
- FiveThirtyEight's post-2020 analysis found polling error has averaged 5-6 points in
  competitive Senate/Governor races. Polls systematically undercount certain demographics.
- Rasmussen Reports has historically shown a ~2-3 point Republican lean compared to
  polling averages (538 bias rating). They were closer to actual results in 2020 than
  many "A-rated" pollsters.
- Online panel polls (YouGov, Morning Consult, Civiqs) tend to skew ~1-2 points more
  Democratic than live-phone polls in the same races.
- Pollsters who are AAPOR/Roper members follow transparency standards but this does not
  guarantee accuracy — several A-rated pollsters had larger errors than C-rated ones in 2020.
""",

    "election": """
STATISTICAL CONTEXT — ELECTION ANOMALIES:
- In 2020, several states saw unprecedented late-counting patterns. In PA, WI, MI, and GA,
  large batches of mail-in ballots were counted after Election Day, shifting margins
  significantly. This was expected due to different states' laws on when mail ballots
  could be processed.
- Bellwether counties: 18 of 19 historically predictive "bellwether" counties voted for
  Trump in 2020, yet Biden won nationally. This broke a pattern dating back decades.
- Down-ballot vs presidential split: Republicans gained 14 House seats in 2020 while
  losing the presidency — the first time since 1960 that the presidential winner's
  party lost House seats.
- Voter turnout in 2020 was 66.8%, the highest since 1900. Both candidates received
  more votes than any prior presidential candidate in history.
- Mail-in voting surged from ~24% in 2016 to ~46% in 2020. Democrats used mail voting
  at 2x the rate of Republicans, creating the "red mirage / blue shift" pattern.
""",

    "prediction market": """
STATISTICAL CONTEXT — PREDICTION MARKETS VS POLLS:
- In 2016, prediction markets gave Hillary Clinton ~85% win probability on election eve.
  Polls showed a ~3 point national lead (which was roughly correct) but markets
  overinterpreted the polling lead as certainty.
- In 2020, prediction markets initially had Trump at ~40% on election night as early
  returns favored him, then swung to Biden >95% as mail ballots were counted.
  Polymarket (launched 2020) tracked this in real time.
- Historically, prediction markets have outperformed polls in 73% of races when there
  is sufficient liquidity (>$100K volume). However, thin markets can be manipulated
  — a single large trader moved a Polymarket contract 8 points in October 2024.
- Key difference: polls measure stated preference; markets measure willingness to bet
  real money. Markets incorporate information beyond polls (early voting data, ground
  game reports, fundraising).
""",

    "trump approval": """
STATISTICAL CONTEXT — PRESIDENTIAL APPROVAL POLLING:
- Trump's 1st term Gallup approval averaged 41%, the lowest for any president since
  Gallup began tracking. However, Rasmussen's daily tracking showed ~5 points higher
  on average, highlighting methodological differences.
- Approval polls using "registered voters" vs "likely voters" vs "adults" produce
  systematically different results. LV screens typically show 2-3 points more
  favorable for Republicans.
- Historical pattern: new presidents start with approval in the 50-60% range (honeymoon
  period). Approval at this point in a term is a weak predictor of reelection — both
  Obama (46%) and Trump (41%) were at similar levels at comparable points.
""",

    "generic ballot": """
STATISTICAL CONTEXT — GENERIC CONGRESSIONAL BALLOT:
- The generic ballot has historically overestimated Democratic performance by ~2-3 points
  due to geographic sorting (Democrats win cities by larger margins than Republicans
  win rural areas, making the popular vote misleading for seat counts).
- In 2022, the generic ballot showed D+0.6 but Republicans won the House popular vote
  by ~2.8 points — a 3.4-point miss favoring Democrats.
- A party typically needs to win the generic ballot by ~3-4 points to gain House seats
  due to structural advantages from redistricting.
""",

    "senate": """
STATISTICAL CONTEXT — SENATE RACE POLLING:
- Senate polls have had an average error of 5.3 points in competitive races since 2016
  (FiveThirtyEight). This means a candidate leading by 5 is essentially in a toss-up.
- In 2022, polls understated Democratic performance in key Senate races (PA, AZ, GA)
  by 2-4 points — a reversal of the 2020 pattern where polls overstated Democrats.
- Incumbency advantage in Senate races has declined from ~6 points (1990s) to ~2 points
  (2020s), making historical comparisons less reliable.
""",

    "governor": """
STATISTICAL CONTEXT — GOVERNOR RACE POLLING:
- Governor races tend to be polled less frequently than Senate or presidential races,
  leading to larger polling errors (average 6.1 points in competitive races).
- Ticket-splitting (voting different parties for governor vs president) remains common
  in governor races — ~30% of states had split results in recent cycles.
""",
}


def get_context_notes(title, summary, source_name):
    """
    Select relevant editorial notes based on article/poll content.
    Returns a string of context notes to inject into the AI prompt.
    """
    text = f"{title} {summary} {source_name}".lower()
    notes = []

    for keyword, note in ANALYSIS_NOTES.items():
        # Check if any word in the keyword appears in the text
        keywords = keyword.split()
        if any(kw in text for kw in keywords):
            notes.append(note.strip())

    # Always include polling context for polls-related queries
    if any(w in text for w in ("poll", "approval", "ballot", "survey", "favorab")):
        polling_note = ANALYSIS_NOTES.get("polling", "")
        if polling_note.strip() not in notes:
            notes.append(polling_note.strip())

    if not notes:
        return ""

    return "\n\n".join(notes)
