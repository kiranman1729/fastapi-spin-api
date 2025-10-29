import json
import sys
import os

def load_json(filename):
    with open(filename, "r") as f:
        return json.load(f)["agents"]

def build_func_param_map(agent_data):
    func_params = {"main": agent_data["main"]["params"][:]}
    for fname, fdata in agent_data["functions"].items():
        func_params[fname] = fdata["params"][:]
    return func_params

def is_match_step(step: str) -> bool:
    return step.strip().startswith("match ")

def parse_match_step(step: str):
    s = step.strip()
    head, rest = s.split(" then ", 1)
    var, val = head.replace("match ", "", 1).split(" = ", 1)
    return var.strip(), val.strip(), rest.strip()

def needs_grouping_for_match_cont(rest_src: str, cont_pi: str) -> bool:
    rs = rest_src.strip()
    cps = cont_pi.strip()
    if rs.startswith("choice "):
        return True
    if "+" in cps or "|" in cps:
        return True
    return False

def step_to_pi_nonmatch(step, agent_name, caller_params, func_param_map):
    s = step.strip()

    if s.startswith("send "):
        msg, chan = s.split(" on ")
        msg = msg.split()[1]
        return f"'{chan}<{msg}>"

    if s.startswith("receive "):
        chan = s.split(" from ")[1]
        var = s.split()[1]
        return f"{chan}({var})"

    if s.startswith("loop to "):
        func = s.split("loop to ")[1].strip()
        callee_args = func_param_map.get(func, caller_params)
        return f"{agent_name}_{func}({', '.join(callee_args)})"

    if s.startswith("choice "):
        branches = [b.strip() for b in s.replace("choice ", "", 1).split(" or ")]
        expanded = []
        for br in branches:
            if br in func_param_map:
                expanded.append(f"{agent_name}_{br}({', '.join(func_param_map[br])})")
            else:
                expanded.append(br)
        return " + ".join(expanded)

    if s.startswith("parallel "):
        branches = [b.strip() for b in s.replace("parallel", "", 1).split("and")]
        return " | ".join([f"{b}()" for b in branches if b])

    if s.startswith("call "):
        func = s.split("call ")[1].strip()
        callee_args = func_param_map.get(func, caller_params)
        return f"{agent_name}_{func}({', '.join(callee_args)})"

    return s

def convert_algorithm_to_pi(alg_str, agent_name, caller_params, func_param_map):
    steps = [s.strip() for s in alg_str.split('.') if s.strip()]
    parts = []
    i = 0
    while i < len(steps):
        s = steps[i]
        if is_match_step(s):
            var0, val0, rest0 = parse_match_step(s)
            j = i + 1
            branches = [(val0, rest0)]
            while j < len(steps) and is_match_step(steps[j]):
                vj, valj, restj = parse_match_step(steps[j])
                if vj != var0:
                    break
                branches.append((valj, restj))
                j += 1

            tail = '.'.join(steps[j:]) if j < len(steps) else ""

            branch_bodies = []
            for val, rest in branches:
                inner_alg = rest + (("." + tail) if tail else "")
                inner_pi = convert_algorithm_to_pi(inner_alg, agent_name, caller_params, func_param_map) if inner_alg else "0"
                if needs_grouping_for_match_cont(rest, inner_pi):
                    cont = f"({inner_pi})"
                else:
                    cont = inner_pi
                branch_bodies.append(f"[{var0}={val}]{cont}")

            if len(branch_bodies) > 1:
                parts.append(f"({' + '.join(branch_bodies)})")
            else:
                parts.append(branch_bodies[0])
            break

        pi_piece = step_to_pi_nonmatch(s, agent_name, caller_params, func_param_map)
        parts.append(pi_piece)
        i += 1

    return '.'.join(parts)

def function_to_pi(agent_name, func_name, func_data, func_param_map):
    params = func_data["params"]
    alg_str = '.'.join(func_data["algorithm"])
    body = convert_algorithm_to_pi(alg_str, agent_name, params, func_param_map)
    return {f"{agent_name}_{func_name}": {"params": params, "body": body}}

def json_to_pi_per_function(agents):
    pi_result = {}
    for agent_name, agent_data in agents.items():
        if agent_name == "System":
            continue
        func_param_map = build_func_param_map(agent_data)
        pi_result.update(function_to_pi(agent_name, "main", agent_data["main"], func_param_map))
        for fname, fdata in agent_data["functions"].items():
            pi_result.update(function_to_pi(agent_name, fname, fdata, func_param_map))
    return pi_result

def system_to_pi(agents):
    system = agents["System"]
    alg = system["main"]["algorithm"][0]
    agent_names = [a.strip() for a in alg.replace("parallel", "", 1).split("and") if a.strip()]
    system_params = system["main"]["params"]
    restricts = ''.join([f"(^" + p + ")" for p in system_params])

    calls = []
    for a in agent_names:
        main_params = agents[a]["main"]["params"]
        calls.append(f"{a}_main({', '.join(main_params)})")

    parallel_expr = " | ".join(calls)
    return {"params": [], "body": f"{restricts} ({parallel_expr})"}



