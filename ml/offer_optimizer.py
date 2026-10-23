"""Finite deterministic search; each candidate is rescored and policy checked."""
from dataclasses import dataclass, replace
import copy

from ml.credit_policy import load_config, precheck, evaluate
from ml.dto import ScoringRequest, CreditDecision, LoanOffer, PublicOffer, PublicResult

@dataclass(frozen=True)
class OptimizationResult:
    decision: CreditDecision
    offers: tuple[LoanOffer, ...]
    candidates_evaluated: int
    def to_dict(self):
        return {"decision": self.decision.to_dict(), "offers": [o.to_dict() for o in self.offers],
                "candidates_evaluated": self.candidates_evaluated}
    def public(self):
        return PublicResult(self.decision.application_id, offers=tuple(
            PublicOffer(o.product_code, o.amount, o.term_months, o.annual_rate, o.monthly_payment)
            for o in self.offers)).to_dict()

def optimize(req, purpose, scorer, investment_object_confirmed=False, config=None):
    cfg = config or load_config()
    req = ScoringRequest.from_payload(req)
    initial = precheck(req, purpose, investment_object_confirmed, cfg)
    if initial:
        return OptimizationResult(initial, (), 0)
    score = scorer.score(req)
    decision = evaluate(req, score, purpose, investment_object_confirmed, cfg)
    if decision.status != "ELIGIBLE":
        return OptimizationResult(decision, (), 0)
    original = req.request
    offers = []
    count = 0
    for product in cfg["purposes"][purpose]:
        limits = cfg["products"][product]
        amounts = {int(original["amount"] * f // cfg["round_amount"] * cfg["round_amount"]) for f in cfg["amount_fractions"]}
        amounts.add(original["amount"])
        terms = set(cfg["term_candidates"] + [original["term_months"], limits["max_term"]])
        for amount in sorted(amounts, reverse=True):
            if not limits["min_amount"] <= amount <= min(original["amount"], limits["max_amount"]):
                continue
            for term in sorted(terms):
                if not limits["min_term"] <= term <= limits["max_term"]:
                    continue
                payload = copy.deepcopy(original)
                payload.update(product_code=product, amount=amount, term_months=term)
                candidate = ScoringRequest(req.application_id, payload)
                if precheck(candidate, purpose, investment_object_confirmed, cfg):
                    continue
                candidate_score = scorer.score(candidate)
                candidate_decision = evaluate(candidate, candidate_score, purpose, investment_object_confirmed, cfg)
                count += 1
                if candidate_decision.status != "ELIGIBLE":
                    continue
                cash = original["company"]["avg_monthly_revenue"] - original["company"]["avg_monthly_expenses"]
                budget = cash / cfg["min_dscr"] - original["company"]["existing_monthly_payments"]
                criteria = {"purpose": 1.0 if product == original["product_code"] else 0.9,
                            "amount": amount / original["amount"],
                            "affordability": max(0.0, min(1.0, 1 - candidate_decision.monthly_payment / max(1, budget))),
                            "term": 1 / (1 + abs(term - original["term_months"]) / 84)}
                rank = sum(cfg["ranking_weights"][key] * value for key, value in criteria.items())
                offers.append(LoanOffer(req.application_id, product, amount, term,
                    candidate_decision.annual_rate, candidate_decision.monthly_payment,
                    candidate_decision.dscr, rank))
    offers.sort(key=lambda o: (-o.rank_score, -o.amount, o.monthly_payment, o.product_code, o.term_months))
    return OptimizationResult(decision, tuple(offers[:cfg["max_offers"]]), count)
