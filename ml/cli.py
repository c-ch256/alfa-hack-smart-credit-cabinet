"""python -m ml.cli score|offers --input request.json; internal output by default."""
import argparse
import json
from pathlib import Path

from ml.dto import ScoringRequest
from ml.inference import ModelScorer
from ml.offer_optimizer import optimize

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["score","offers"])
    parser.add_argument("--input",type=Path)
    parser.add_argument("--purpose",choices=["working_capital","equipment","expansion"],default="working_capital")
    parser.add_argument("--investment-object-confirmed",action="store_true")
    parser.add_argument("--public",action="store_true")
    args=parser.parse_args()
    if args.action=="score" and args.public:
        parser.error("Internal scores cannot be emitted through the public DTO")
    if args.input:
        payload=json.loads(args.input.read_text(encoding="utf-8"))
    else:
        payload=json.loads(Path("ml/contracts/scoring_requests.json").read_text(encoding="utf-8"))[0]["request"]
    req=ScoringRequest.from_payload(payload)
    scorer=ModelScorer()
    if args.action=="score":
        result=scorer.score(req).to_dict()
    else:
        optimized=optimize(req,args.purpose,scorer,args.investment_object_confirmed)
        result=optimized.public() if args.public else optimized.to_dict()
    print(json.dumps(result,indent=2,ensure_ascii=False))

if __name__=="__main__":
    main()
