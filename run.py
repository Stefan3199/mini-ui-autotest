from engine.case_loader import load_cases, load_config
from engine.request_sender import send_case

cases = load_cases()
config = load_config()
RETRY_COUNT = config["retry_count"]

passed = 0
failed = 0
flaky = 0
results = []

for case in cases:
    result = None
    for attempt in range(RETRY_COUNT):
        result = send_case(case)
        if result is not None:
            break   # 拿到判决了，这趟不用再试，跳出发几次的圈
    if result is None:
        flaky = flaky + 1
        print(case["name"], "环境抖动")
        results.append({'name':case["name"],'result':'FLAKY','expect':case["expect"]["code"],'actual':None})# 发了三次连判决都没有：记环境抖动
    elif result["code"] == case["expect"]["code"]:
        passed=passed+1      # 老三样：passed 加一，打 用例名 PASS
        print(case["name"],'PASS')
        results.append({'name':case["name"],'result':'PASS','expect':case["expect"]["code"],'actual':result["code"]})
    else:
        failed=failed+1
        print(case["name"],'FAIL')
        results.append({'name':case["name"],'result':failed,'expect':case["expect"]["code"],'actual':result["code"]})# 老三样：failed 加一，打 用例名 FAIL

print(f"共{len(cases)}条，过{passed}，挂{failed}，抖{flaky}")
print(results)