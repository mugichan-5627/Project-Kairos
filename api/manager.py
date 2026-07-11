from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from _kairos import bootstrap, query_params, read_sql, send_json


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        bootstrap()
        params = query_params(self)
        manager_id = params.get("manager_id")
        name = params.get("name")
        if manager_id:
            manager = read_sql("SELECT * FROM manager_identity WHERE manager_id=?", (manager_id,))
        elif name:
            manager = read_sql("SELECT * FROM manager_identity WHERE canonical_name LIKE ?", (f"%{name}%",))
        else:
            manager = read_sql("SELECT * FROM manager_identity ORDER BY canonical_name LIMIT 25")
        ids = manager["manager_id"].tolist() if not manager.empty and "manager_id" in manager else []
        tenures = read_sql("SELECT * FROM manager_tenure WHERE manager_id IN ({})".format(",".join(["?"] * len(ids))), tuple(ids)) if ids else read_sql("SELECT * FROM manager_tenure WHERE 1=0")
        pas = read_sql("SELECT * FROM portable_alpha_scores WHERE manager_id IN ({})".format(",".join(["?"] * len(ids))), tuple(ids)) if ids else read_sql("SELECT * FROM portable_alpha_scores WHERE 1=0")
        send_json(self, {"managers": manager.to_dict("records"), "tenures": tenures.to_dict("records"), "portable_alpha": pas.to_dict("records")})
