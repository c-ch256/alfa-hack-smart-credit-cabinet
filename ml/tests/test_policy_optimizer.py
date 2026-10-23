"""Deterministic policy tests with controlled PD plus real end-to-end examples."""
import copy
import json
from pathlib import Path

import pytest

from ml.credit_policy import load_config, evaluate, precheck, monthly_payment, risk_band
from ml.dto import ScoringRequest, ScoringResult, fingerprint, PublicResult
from ml.errors import InputValidationError
from ml.features import schema_validator
from ml.inference import ModelScorer
from ml.offer_optimizer import optimize

@pytest.fixture
def application():
    raw=json.loads(Path("ml/contracts/scoring_requests.json").read_text(encoding="utf-8"))[0]["request"]
    return ScoringRequest("application-policy-test",copy.deepcopy(raw))

class FixedScorer:
    def __init__(self,pd=.01):
        self.pd=pd
        self.calls=0
    def score(self,application):
        self.calls+=1
        return ScoringResult(application.application_id,self.pd,risk_band(self.pd),"unit-fixed",
            [],fingerprint(application.request),{})

def test_low_pd_alone_does_not_approve(application):
    application.request["company"]["avg_monthly_expenses"]=application.request["company"]["avg_monthly_revenue"]
    scorer=FixedScorer()
    decision=evaluate(application,scorer.score(application),"working_capital")
    assert decision.status == "DECLINED"
    assert decision.codes == ("INSUFFICIENT_CASH_FLOW",)

@pytest.mark.parametrize("pd,status",[(.119999,"ELIGIBLE"),(.12,"MANUAL_REVIEW"),(.249999,"MANUAL_REVIEW"),(.25,"DECLINED")])
def test_pd_policy_boundaries(application,pd,status):
    assert evaluate(application,FixedScorer(pd).score(application),"working_capital").status == status

@pytest.mark.parametrize("overdue,status",[(30,"ELIGIBLE"),(31,"MANUAL_REVIEW"),(90,"MANUAL_REVIEW"),(91,"DECLINED")])
def test_overdue_boundaries(application,overdue,status):
    application.request["bki"]["max_overdue_days_12m"]=overdue
    scorer=FixedScorer()
    assert evaluate(application,scorer.score(application),"working_capital").status == status

def test_stop_factor_before_scoring(application):
    application.request["fns"]["status"]="liquidating"
    scorer=FixedScorer()
    result=optimize(application,"working_capital",scorer)
    assert result.decision.status == "DECLINED"
    assert not result.offers and scorer.calls == 0

@pytest.mark.parametrize("pd,status",[(.2,"MANUAL_REVIEW"),(.4,"DECLINED")])
def test_no_offers_for_ineligible(application,pd,status):
    result=optimize(application,"working_capital",FixedScorer(pd))
    assert result.decision.status == status and not result.offers
    assert result.candidates_evaluated == 0

def test_insufficient_investment_data(application):
    application.request.update(product_code="investment",amount=8000000,term_months=60,collateral_value=0)
    scorer=FixedScorer()
    result=optimize(application,"equipment",scorer)
    assert result.decision.status == "INSUFFICIENT_DATA"
    assert scorer.calls == 0 and not result.offers
    result=optimize(application,"equipment",scorer,investment_object_confirmed=True)
    assert result.decision.codes == ("COLLATERAL_REQUIRED",) and not result.offers

def test_purpose_and_limits(application):
    assert optimize(application,"equipment",FixedScorer()).decision.codes == ("PRODUCT_PURPOSE_MISMATCH",)
    assert optimize(application,"unknown",FixedScorer()).decision.status == "INSUFFICIENT_DATA"
    assert optimize(application,None,FixedScorer()).decision.status == "INSUFFICIENT_DATA"
    application.request["amount"]=299999
    assert optimize(application,"working_capital",FixedScorer()).decision.codes == ("PRODUCT_LIMIT",)

def test_annuity():
    assert monthly_payment(1200,12,0) == 100
    # Independent check: amortize principal by monthly interest and payments.
    payment = monthly_payment(3000000,24,.22)
    balance = 3000000.0
    for _ in range(24):
        balance = balance * (1 + .22/12) - payment
    assert balance == pytest.approx(0,abs=1e-7)
    with pytest.raises(InputValidationError): monthly_payment(-1,12,.2)

def test_dscr_exact_boundary(application):
    scorer=FixedScorer()
    original=evaluate(application,scorer.score(application),"working_capital")
    cfg=load_config()
    cfg["min_dscr"]=original.dscr
    assert evaluate(application,scorer.score(application),"working_capital",config=cfg).status == "ELIGIBLE"
    cfg["min_dscr"]+=1e-9
    assert evaluate(application,scorer.score(application),"working_capital",config=cfg).status == "DECLINED"

def test_score_binding(application):
    scoring=FixedScorer().score(application)
    application.request["amount"]+=10000
    with pytest.raises(InputValidationError): evaluate(application,scoring,"working_capital")

@pytest.mark.parametrize("purpose,product,investment", [
    ("working_capital","working_capital",False),("equipment","investment",True)])
def test_optimizer_valid_ranked_offers(application,purpose,product,investment):
    application.request.update(product_code=product,term_months=36,collateral_value=4000000)
    scorer=FixedScorer()
    result=optimize(application,purpose,scorer,investment)
    assert result.decision.status == "ELIGIBLE" and result.offers
    assert [o.rank_score for o in result.offers] == sorted([o.rank_score for o in result.offers],reverse=True)
    for offer in result.offers:
        assert offer.product_code == product and offer.amount <= application.request["amount"]
        assert offer.dscr >= load_config()["min_dscr"]
        assert 0<=offer.rank_score<=1
        schema_validator("loan_offer.schema.json").validate(offer.to_dict())
        candidate=copy.deepcopy(application.request)
        candidate.update(product_code=offer.product_code,amount=offer.amount,term_months=offer.term_months)
        envelope=ScoringRequest(application.application_id,candidate)
        assert evaluate(envelope,scorer.score(envelope),purpose,investment).status == "ELIGIBLE"
    schema_validator("credit_decision.schema.json").validate(result.decision.to_dict())
    public=result.public()
    assert PublicResult.from_dict(public).to_dict() == public
    assert "pd" not in json.dumps(public) and "dscr" not in json.dumps(public)

def test_empty_candidate_set(application):
    class ChangingScorer(FixedScorer):
        def score(self,req):
            self.pd=.01 if self.calls==0 else .4
            return super().score(req)
    result=optimize(application,"working_capital",ChangingScorer())
    assert result.decision.status=="ELIGIBLE" and not result.offers
    assert result.candidates_evaluated>0

def test_real_model_integration(application):
    scorer=ModelScorer()
    result=optimize(application,"working_capital",scorer)
    assert result.decision.status=="ELIGIBLE" and result.offers
    declined=copy.deepcopy(application)
    declined.request["bki"]["defaults"]=1
    assert not optimize(declined,"working_capital",scorer).offers
