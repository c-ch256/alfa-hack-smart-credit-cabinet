"""Measure full single/batch scoring and write real demonstration examples."""
import copy
import json
from pathlib import Path
import platform
import time

import numpy as np
import psutil

from ml.dto import ScoringRequest
from ml.features import CONTRACT_DIR
from ml.inference import ModelScorer
from ml.offer_optimizer import optimize

def main():
    process=psutil.Process()
    rss_before=process.memory_info().rss
    start=time.perf_counter()
    scorer=ModelScorer()
    load_ms=(time.perf_counter()-start)*1000
    examples=json.loads((CONTRACT_DIR/"scoring_requests.json").read_text(encoding="utf-8"))
    requests=[ScoringRequest("demo-"+str(i),e["request"]) for i,e in enumerate(examples)]
    results=[{"scenario":e["scenario"],"scoring":scorer.score(req).to_dict()} for e,req in zip(examples,requests)]
    for _ in range(5): scorer.score(requests[0])
    timings=[]
    for i in range(100):
        start=time.perf_counter()
        scorer.score(requests[i%len(requests)])
        timings.append((time.perf_counter()-start)*1000)
    start=time.perf_counter()
    scorer.score_many(requests*10)
    batch_ms=(time.perf_counter()-start)*1000
    working=optimize(requests[0],"working_capital",scorer)
    investment=copy.deepcopy(requests[0].request)
    investment.update(product_code="investment",amount=3000000,term_months=36,collateral_value=4000000)
    invest=optimize(ScoringRequest("demo-investment",investment),"equipment",scorer,True)
    refused=copy.deepcopy(requests[0].request)
    refused["fns"]["status"]="liquidating"
    declined=optimize(ScoringRequest("demo-declined",refused),"working_capital",scorer)
    output=Path("ml/reports")
    output.mkdir(parents=True,exist_ok=True)
    report={"load_ms":load_ms,"single_call_p50_ms":float(np.percentile(timings,50)),
            "single_call_p95_ms":float(np.percentile(timings,95)),"iterations":100,
            "batch_size":30,"batch_ms":batch_ms,"rss_before_bytes":rss_before,
            "rss_after_bytes":process.memory_info().rss,"peak_working_set_bytes":getattr(process.memory_info(),"peak_wset",None),
            "platform":platform.platform(),"processor":platform.processor(),
            "logical_cpus":psutil.cpu_count(),"python":platform.python_version(),
            "includes":"validation, pipeline preprocessing, predict_proba, coefficient contributions, result schema",
            "sla":"No approved SLA; measurements describe this machine only."}
    (output/"benchmark.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    (output/"backend_examples.json").write_text(json.dumps(results,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    demo={"working_capital":working.to_dict(),"investment":invest.to_dict(),"declined":declined.to_dict(),
          "public_working_capital":working.public()}
    (output/"offer_examples.json").write_text(json.dumps(demo,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))
    print(json.dumps(demo,indent=2,ensure_ascii=False))

if __name__=="__main__":
    main()
