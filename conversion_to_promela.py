from lark import Lark, Transformer, v_args

# --- 1) Grammar ---
pi_grammar = r"""
    ?start: process

    ?process: choice
    ?choice: parallel
           | choice "+" parallel              -> choice
    ?parallel: seq
             | parallel "|" seq               -> parallel

    ?seq: atom
        | prefix "." seq                      -> action

    ?atom: restriction
         | call
         | "0"                                -> nil
         | match_expr
         | "(" process ")"
         | prefix                             -> prefix_process

    # Matches
    match_expr: "[" name "=" name "]" seq -> match

    prefix: input
          | output
          | "t"                                -> tau

    input       : CNAME "(" name ")"
    output      : "'" CNAME "<" name ">"
    restriction : "(^" (CNAME|ANAME) ")" process

    call        : ANAME "(" [params] ")"
    params      : name ("," name)*
    name        : CNAME | ANAME

    ANAME: /[A-Z][A-Za-z0-9_]*/
    CNAME: /[a-z][A-Za-z0-9_]*/

    %import common.WS
    %ignore WS
"""


# --- 2) AST Nodes ---
class Node: pass
class Nil(Node): pass
class Tau(Node): pass
class Input(Node):  
    def __init__(self, chan, var): self.chan, self.var = chan, var
class Output(Node):  
    def __init__(self, chan, msg): self.chan, self.msg = chan, msg
class Action(Node):  
    def __init__(self, prefix, cont): self.prefix, self.cont = prefix, cont
class Call(Node):  
    def __init__(self, name, params): self.name, self.params = name, params
class Restriction(Node):  
    def __init__(self, name, body): self.name, self.body = name, body
class Choice(Node):  
    def __init__(self, left, right): self.left, self.right = left, right
class Parallel(Node):  
    def __init__(self, left, right): self.left, self.right = left, right
class Match(Node):  
    def __init__(self, left, right, body): self.left, self.right, self.body = left, right, body

# --- 3) Transformer ---
@v_args(inline=True)
class PromelaASTBuilder(Transformer):
    def nil(self, *a): return Nil()
    def tau(self, *a): return Tau()
    def input(self, c, v): return Input(str(c), str(v))
    def output(self, c, m): return Output(str(c), str(m))
    def action(self, p, proc): return Action(p, proc)
    def call(self, n, params=None): return Call(str(n), [str(p) for p in (params or [])])
    def restriction(self, n, proc): return Restriction(str(n), proc)
    def choice(self, p1, p2): return Choice(p1, p2)
    def parallel(self, p1, p2): return Parallel(p1, p2)
    def match(self, l, r, p): return Match(str(l), str(r), p)
    def prefix_process(self, p): return p
    def prefix(self, p): return p
    def params(self, *items): return [str(i) for i in items]
    def name(self, t): return str(t)
    def ANAME(self, t): return str(t)
    def CNAME(self, t): return str(t)

# --- 4) Utils ---
def is_pure_call(node):
    return isinstance(node, Call)

def collect_bound_names_from_inputs(node, bound):
    if isinstance(node, Input):
        bound.add(node.var)
    elif isinstance(node, Restriction):
        collect_bound_names_from_inputs(node.body, bound)
    elif isinstance(node, Action):
        collect_bound_names_from_inputs(node.prefix, bound)
        collect_bound_names_from_inputs(node.cont, bound)
    elif isinstance(node, Match):
        collect_bound_names_from_inputs(node.body, bound)
    elif isinstance(node, Choice):
        collect_bound_names_from_inputs(node.left, bound)
        collect_bound_names_from_inputs(node.right, bound)
    # do not descend into Parallel/Choice (locals handled in helpers)

def hoist_top_level_restrictions(ast):
    names, node = [], ast
    while isinstance(node, Restriction):
        names.append(node.name)
        node = node.body
    return node, names

# --- 5) Codegen ---
def emit(node, parent_name, current_process, helpers, helper_count, parent_params):
    if isinstance(node, Nil): return "skip;"
    if isinstance(node, Tau): return "skip;"
    if isinstance(node, Input): return f"{node.chan}?{node.var}"
    if isinstance(node, Output): return f"{node.chan}!{node.msg}"
    if isinstance(node, Action):
        # special: propagate bound vars from input into helper params
        if isinstance(node.prefix, Input):
            new_params = parent_params + [node.prefix.var]
        else:
            new_params = parent_params
        return f"{emit(node.prefix, parent_name, current_process, helpers, helper_count, parent_params)};\n{emit(node.cont, parent_name, current_process, helpers, helper_count, new_params)}"
    if isinstance(node, Call):
        params = ", ".join(node.params)
        if node.name == parent_name and current_process == parent_name:
            return f"goto {parent_name}_loop;"
        return f"run {node.name}({params});"
    if isinstance(node, Choice):
        # --- flatten nested choice ---
        branches = []
        def flatten(ch):
            if isinstance(ch, Choice):
                flatten(ch.left)
                flatten(ch.right)
            else:
                branches.append(ch)
        flatten(node)

        # Emit each branch
        branch_code = []
        for br in branches:
            branch_code.append(
                emit(br, parent_name, current_process, helpers, helper_count, parent_params)
            )
        # Generate if-guarded nondeterministic choice
        return "if\n" + "\n".join([f":: {c}" for c in branch_code]) + "\nfi;"

    if isinstance(node, Parallel):
        if current_process == parent_name:
            # --- flatten nested parallel ---
            branches = []
            def flatten(par):
                if isinstance(par, Parallel):
                    flatten(par.left)
                    flatten(par.right)
                else:
                    branches.append(par)
            flatten(node)

            # --- Case 1: all branches are pure calls ---
            if all(is_pure_call(b) for b in branches):
                runs = [emit(b, parent_name, current_process,
                             helpers, helper_count, parent_params)
                        for b in branches]
                return "atomic { " + "; ".join(runs) + " }"

            # --- Case 2: at least one branch is not a pure call ---
            else:
                runs = []
                for branch in branches:
                    helper_count[0] += 1
                    hname = f"{parent_name}__{helper_count[0]}"
                    branch_bound = set()
                    collect_bound_names_from_inputs(branch, branch_bound)
                    decls = [bn for bn in sorted(branch_bound)
                             if bn not in parent_params]
                    body = emit(branch, parent_name, hname,
                                helpers, helper_count, parent_params + decls)
                    param_sig = f"chan {', '.join(parent_params+decls)}" if parent_params+decls else ""
                    decl_lines = "".join([f"  chan {bn};\n" for bn in decls])
                    helpers.append(
                        f"proctype {hname}({param_sig}) {{\n"
                        f"{decl_lines}"
                        f"   {hname}_loop:\n"
                        f"  {body}\n"
                        f"}}"
                    )
                    run_args = ", ".join(parent_params+decls)
                    runs.append(f"run {hname}({run_args})")
                return "atomic { " + "; ".join(runs) + " }"
        else:
            # Non-top-level parallel: just emit inline atomic
            left = emit(node.left, parent_name, current_process,
                        helpers, helper_count, parent_params)
            right = emit(node.right, parent_name, current_process,
                         helpers, helper_count, parent_params)
            return f"atomic {{ {left}; {right} }}"

    if isinstance(node, Match):
        body = emit(node.body, parent_name, current_process, helpers, helper_count, parent_params)
        return f"({node.left} == {node.right}) ->\n  {body}"
    if isinstance(node, Restriction):
        helper_count[0] += 1
        hname = f"{parent_name}__{helper_count[0]}"
        branch_bound = set()
        collect_bound_names_from_inputs(node.body, branch_bound)
        decls = [bn for bn in sorted(branch_bound) if bn not in parent_params and bn != node.name]
        body = emit(node.body, parent_name, hname, helpers, helper_count, parent_params + decls)
        param_sig = f"chan {', '.join(parent_params +  decls)}" if parent_params + decls else ""
        decl_lines = "".join([f"  chan {bn};\n" for bn in decls])
        helpers.append(
            f"proctype {hname}({param_sig}) {{\n"
            f"  chan {node.name} = [0] of {{ chan }};\n"
            f"{decl_lines}"
            f"  {hname}_loop:\n"
            f"    {body}\n"
            f"}}"
        )
        run_args = ", ".join(parent_params + decls)
        return f"run {hname}({run_args});"
    raise NotImplementedError(f"Unknown node: {node}")

# --- 6) Translator ---
def translate_agent(agent_name, params, body_src):
    parser = Lark(pi_grammar, parser="lalr")
    tree = parser.parse(" ".join(body_src.strip().split()))
    ast = PromelaASTBuilder().transform(tree)

    core_ast, top_restricts = hoist_top_level_restrictions(ast)

    helpers, helper_count = [], [0]
    body_code = emit(core_ast, agent_name, agent_name, helpers, helper_count, params)

    bound = set()
    collect_bound_names_from_inputs(core_ast, bound)
    input_decls = [f"  chan {bn};" for bn in bound if bn not in params]

    restrict_decls = [f"  chan {rn} = [0] of {{ chan }};" for rn in top_restricts]

    param_str = f"(chan {', '.join(params)})" if params else "()"
    code = [f"proctype {agent_name}{param_str} {{"]
    if restrict_decls: code.extend(restrict_decls)
    if input_decls: code.extend(input_decls)
    code.append(f"  {agent_name}_loop:")
    code.append("    " + "\n    ".join(body_code.splitlines()))
    code.append("}")
    if helpers: code.append("\n".join(helpers))
    return "\n".join(code)


def convert_pi_to_promela(input_json: dict) -> str:
    agents = input_json["agents"]
    full = []
    for n, d in agents.items():
        promela = translate_agent(n, d["params"], d["body"])
        full.append(promela)

    # Build init block
    init_expr = "System_main()"
    parser = Lark(pi_grammar, parser="lalr")
    tree = parser.parse(" ".join(init_expr.strip().split()))
    ast = PromelaASTBuilder().transform(tree)

    core_ast, top_restricts = hoist_top_level_restrictions(ast)

    helpers, helper_count = [], [0]
    body_code = emit(core_ast, "__init", "__init", helpers, helper_count, [])

    bound = set()
    collect_bound_names_from_inputs(core_ast, bound)
    input_decls = [f"  chan {bn};" for bn in bound]
    restrict_decls = [f"  chan {rn} = [0] of {{ chan }};" for rn in top_restricts]

    init_code = ["init {"]
    if restrict_decls: init_code.extend(restrict_decls)
    if input_decls: init_code.extend(input_decls)
    init_code.append("  " + "\n  ".join(body_code.splitlines()))
    init_code.append("}")
    if helpers: init_code.append("\n".join(helpers))
    init_block = "\n".join(init_code)

    final_model = "\n\n// --- COMPLETE PROMELA MODEL ---\n\n" + \
                  "\n\n// -----------------------------\n\n".join(full + [init_block])

    return final_model
