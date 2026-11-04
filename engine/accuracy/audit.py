"""What the production engine already does. The accuracy experiment does not add a second copy."""

from __future__ import annotations

PRODUCTION_FACTORS = (
    {
        "factor": "Minutes",
        "where": "MinutesModel.project",
        "affects": "Expected minutes, which scale shot volume, rebound chances, and assist chances.",
        "notes": "Blends season, last-5, and last-10 minutes. Starters have a floor. A back-to-back multiplies minutes by 0.94. Blowout risk multiplies minutes by 1 - 0.06 * risk. Teammate-absence minutes are added once.",
    },
    {
        "factor": "Blowout risk",
        "where": "MinutesModel and UsageModel",
        "affects": "Minutes and usage. It is not applied again as a separate scoring multiplier.",
        "notes": "The accuracy experiment does not add another blowout term. Historical pregame spreads were not used to invent a second adjustment.",
    },
    {
        "factor": "Usage",
        "where": "UsageModel and the points/threes volume factor",
        "affects": "Shot volume only when tonight's usage differs from the player's own usage. Season usage is already inside season field-goal attempts.",
        "notes": "Teammate-absence usage_vacuum is a multiplier. Historical injury reports are not available, so backtests leave that multiplier at 1.",
    },
    {
        "factor": "Opportunity and conversion",
        "where": "Points, assists, rebounds, and threes models",
        "affects": "Mean. About 80% is opportunities times conversion. About 20% is a minute-scaled rate prior.",
        "notes": "Points use field-goal attempts and points per attempt. Rebounds use rebound chances and a secure rate. Assists need potential assists. Threes use attempt rate and make rate.",
    },
    {
        "factor": "Opponent pace",
        "where": "BaseModel pace factor via resolve_pace",
        "affects": "Opportunity volume. A missing pace widens the standard deviation and is labeled a fallback.",
        "notes": "Model A holds pace at the league average so pace is not confused with positional defense.",
    },
    {
        "factor": "Defensive rating",
        "where": "defense_rating_factor",
        "affects": "Scoring efficiency, partially. It is not multiplied on top of fantasy points allowed.",
        "notes": "The factor is the opponent rating divided by the league rating, clamped.",
    },
    {
        "factor": "Positional defense",
        "where": "MatchupModel.positional_defense_factor",
        "affects": "A partial move of volume or efficiency. Points use 55% of the points-allowed gap. Threes use attempt volume when the slot is measured.",
        "notes": "Fantasy points allowed are not applied to points, rebounds, assists, or threes. January and February tests found this adjustment hurt probability accuracy.",
    },
    {
        "factor": "Injuries and role changes",
        "where": "InjuryRedistributionModel, minutes_vacuum, usage_vacuum",
        "affects": "Minutes and usage on the live slate when current injuries are known.",
        "notes": "BALLDONTLIE injuries are current-only. Historical backtests do not invent them.",
    },
    {
        "factor": "Variance",
        "where": "Distribution on StatProjection",
        "affects": "The probability of a line, not the mean. Pace fallback widens the standard deviation.",
        "notes": "PRA uses a correlated sum of points, rebounds, and assists. Pushes are graded as pushes.",
    },
    {
        "factor": "Market calibration",
        "where": "calibrate_true_probability",
        "affects": "The live displayed probability after the statistical probability is computed.",
        "notes": "The accuracy experiment does not blend sportsbook prices into its probabilities. No-vig prices are an external benchmark only.",
    },
)

MISSING_HISTORICAL_FIELDS = (
    "Game-level potential assists are not on GET /nba/v2/stats/advanced. Season passing averages include potential_ast, but a 2025-26 season total includes later games and is not used.",
    "Historical injury and lineup reports are not available. Usage and minutes vacuums stay neutral.",
    "BALLDONTLIE positions are G/F/C and hybrids, mapped explicitly onto five positions. That mapping is uncertain.",
    "Pregame blowout spread is not on the box score. The experiment does not add a second blowout adjustment.",
)
