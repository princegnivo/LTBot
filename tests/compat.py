"""Détecte les f-strings valides seulement en Python >= 3.12 (guillemets identiques / antislash / commentaire dans l'expression)."""
import io, sys, tokenize
def check(path):
    src = open(path, encoding="utf-8").read()
    bad = []
    stack = []   # pile de (quote_char, depth_braces)
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    for t in toks:
        if t.type == tokenize.FSTRING_START:
            q = t.string[-3:] if t.string.endswith(('"""', "'''")) else t.string[-1]
            stack.append([q, 0])
        elif t.type == tokenize.FSTRING_END:
            stack.pop()
        elif stack:
            top = stack[-1]
            if t.type == tokenize.OP and t.string == "{" and True:
                top[1] += 1
            elif t.type == tokenize.OP and t.string == "}":
                top[1] = max(0, top[1] - 1)
            elif top[1] > 0 and t.type != tokenize.FSTRING_MIDDLE:
                s = t.string
                if "\\" in s:
                    bad.append((t.start[0], "antislash dans l'expression", s[:30]))
                if t.type == tokenize.STRING and s[:1] in "\"'" and s[0] == top[0][0] and len(top[0]) == 1:
                    bad.append((t.start[0], "guillemet identique dans l'expression", s[:30]))
                if t.type == tokenize.FSTRING_START and t.string[-1] == top[0][-1] and len(top[0]) == 1:
                    bad.append((t.start[0], "f-string imbriquée de même guillemet", t.string))
    return bad
if __name__ == "__main__":
    n = 0
    for p in sys.argv[1:]:
        for line, why, snip in check(p):
            n += 1
            print(f"{p}:{line}: {why}: {snip}")
    print("problèmes:", n)
