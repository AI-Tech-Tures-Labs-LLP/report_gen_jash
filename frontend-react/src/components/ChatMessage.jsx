import { useState, useMemo } from "react";
import { format } from "sql-formatter";

// ─── SQL keyword / function sets ──────────────────────────────────────────────

const KW = new Set([
  'SELECT','FROM','WHERE','JOIN','ON','GROUP','BY','ORDER','HAVING','LIMIT',
  'OFFSET','INNER','LEFT','RIGHT','OUTER','CROSS','FULL','NATURAL','UNION',
  'INTERSECT','EXCEPT','ALL','DISTINCT','AS','AND','OR','NOT','IN','IS',
  'NULL','LIKE','ILIKE','BETWEEN','EXISTS','CASE','WHEN','THEN','ELSE','END',
  'ASC','DESC','INSERT','INTO','VALUES','UPDATE','SET','DELETE','CREATE',
  'TABLE','VIEW','INDEX','DROP','ALTER','ADD','COLUMN','PRIMARY','KEY',
  'FOREIGN','REFERENCES','UNIQUE','DEFAULT','CONSTRAINT','CHECK','WITH',
  'OVER','PARTITION','FILTER','ROWS','RANGE','PRECEDING','FOLLOWING',
  'CURRENT','ROW','UNBOUNDED','TRUE','FALSE','NULLS','FIRST','LAST',
  'RETURNING','USING','LATERAL','WINDOW','RECURSIVE','MATERIALIZED',
]);

const FN = new Set([
  'SUM','COUNT','AVG','MIN','MAX','COALESCE','NULLIF','ISNULL','IFNULL','NVL',
  'ROUND','FLOOR','CEIL','CEILING','ABS','CAST','CONVERT','EXTRACT',
  'DATE_TRUNC','DATE_PART','TO_DATE','TO_CHAR','TO_NUMBER','TO_TIMESTAMP',
  'TRIM','LTRIM','RTRIM','UPPER','LOWER','INITCAP','SUBSTRING','SUBSTR',
  'LENGTH','LEN','CONCAT','REPLACE','STRING_AGG','ARRAY_AGG','JSON_AGG',
  'LISTAGG','RANK','ROW_NUMBER','DENSE_RANK','NTILE','LEAD','LAG',
  'FIRST_VALUE','LAST_VALUE','NTH_VALUE','PERCENT_RANK','CUME_DIST',
  'NOW','CURRENT_DATE','CURRENT_TIMESTAMP','DATEDIFF','DATEADD',
  'YEAR','MONTH','DAY','HOUR','MINUTE','SECOND','GENERATE_SERIES',
  'GREATEST','LEAST','POWER','SQRT','MOD','UNNEST','ARRAY_LENGTH',
  'REGEXP_MATCHES','REGEXP_REPLACE','POSITION','IIF','DECODE',
]);

// ─── Tokenizer ────────────────────────────────────────────────────────────────

function tokenizeSQL(sql) {
  const tokens = [];
  let i = 0;
  while (i < sql.length) {
    const ch = sql[i];

    // Block comment /* ... */
    if (ch === '/' && sql[i + 1] === '*') {
      const end = sql.indexOf('*/', i + 2);
      const text = end < 0 ? sql.slice(i) : sql.slice(i, end + 2);
      tokens.push({ t: 'comment', v: text });
      i += text.length;
      continue;
    }

    // Line comment --
    if (ch === '-' && sql[i + 1] === '-') {
      const end = sql.indexOf('\n', i);
      const text = end < 0 ? sql.slice(i) : sql.slice(i, end);
      tokens.push({ t: 'comment', v: text });
      i += text.length;
      continue;
    }

    // String literal 'text' (handles '' escaped quotes)
    if (ch === "'") {
      let j = i + 1;
      while (j < sql.length) {
        if (sql[j] === "'" && sql[j + 1] === "'") { j += 2; }
        else if (sql[j] === "'") { j++; break; }
        else j++;
      }
      tokens.push({ t: 'string', v: sql.slice(i, j) });
      i = j;
      continue;
    }

    // Quoted identifier "name"
    if (ch === '"') {
      let j = i + 1;
      while (j < sql.length && sql[j] !== '"') j++;
      tokens.push({ t: 'other', v: sql.slice(i, j + 1) });
      i = j + 1;
      continue;
    }

    // Number
    if (/[0-9]/.test(ch) || (ch === '.' && /[0-9]/.test(sql[i + 1] || ''))) {
      const m = sql.slice(i).match(/^[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?/);
      if (m) { tokens.push({ t: 'number', v: m[0] }); i += m[0].length; continue; }
    }

    // PostgreSQL cast ::
    if (ch === ':' && sql[i + 1] === ':') {
      tokens.push({ t: 'operator', v: '::' });
      i += 2;
      continue;
    }

    // Word → keyword, function, or plain identifier
    if (/[a-zA-Z_]/.test(ch)) {
      const m = sql.slice(i).match(/^[a-zA-Z_]\w*/);
      const word = m[0];
      const up = word.toUpperCase();
      const ahead = sql.slice(i + word.length).trimStart();
      const type = FN.has(up) && ahead[0] === '(' ? 'function'
                 : KW.has(up) ? 'keyword'
                 : 'other';
      tokens.push({ t: type, v: word });
      i += word.length;
      continue;
    }

    // Multi-char operators  <=  >=  <>  !=
    if (/[<>!]/.test(ch) && /[=>]/.test(sql[i + 1] || '')) {
      tokens.push({ t: 'operator', v: ch + sql[i + 1] });
      i += 2;
      continue;
    }

    // Single-char operator
    if (/[=<>+\-*/%^&|~]/.test(ch)) {
      tokens.push({ t: 'operator', v: ch });
      i++;
      continue;
    }

    // Whitespace, punctuation, everything else
    tokens.push({ t: 'other', v: ch });
    i++;
  }
  return tokens;
}

// ─── Color scheme ─────────────────────────────────────────────────────────────
// t.code is #0f172a (dark navy) in BOTH light and dark app themes, so we always
// need bright/light colors here — never dark text on a dark background.

const CODE_COLORS = {
  keyword:  '#569cd6',  // blue   — SELECT FROM WHERE JOIN ORDER GROUP
  function: '#dcdcaa',  // yellow — SUM COUNT AVG ROUND etc.
  string:   '#ce9178',  // orange — 'string literals'
  number:   '#b5cea8',  // green  — 10  1.5  0.99
  comment:  '#7ca668',  // muted green — -- inline comments
  operator: '#d4d4d4',  // light grey  — = < > <> ::
  other:    '#d4d4d4',  // light grey  — column/table names, punctuation
};

// ─── SqlBlock ─────────────────────────────────────────────────────────────────

function SqlBlock({ sql, t }) {
  const clr = CODE_COLORS;

  const formatted = useMemo(() => {
    try {
      return format(sql, { language: 'postgresql', tabWidth: 4, keywordCase: 'upper', indentStyle: 'standard' });
    } catch {
      return sql;
    }
  }, [sql]);

  const tokens = useMemo(() => tokenizeSQL(formatted), [formatted]);

  return (
    <div style={{
      padding: '0.6rem 0.75rem',
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
      fontSize: '0.81rem',
      lineHeight: 1.75,
      overflowX: 'auto',
      whiteSpace: 'pre',
    }}>
      {tokens.map((tok, i) => (
        <span key={i} style={{ color: clr[tok.t] || clr.other }}>{tok.v}</span>
      ))}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function ChatMessage({ msg, t, themeMode }) {
  if (msg.role === "user") {
    return (
      <div style={{ display: "flex", justifyContent: "flex-end", margin: "0.6rem 0" }}>
        <div
          style={{
            background: t.userBubble,
            color: t.userText,
            WebkitTextFillColor: t.userText,
            padding: "0.7rem 1rem",
            borderRadius: "14px 14px 4px 14px",
            maxWidth: "75%",
            fontSize: "0.92rem",
            lineHeight: 1.5,
            whiteSpace: "pre-wrap",
          }}
        >
          {msg.text}
        </div>
      </div>
    );
  }

  const d = msg.data || {};
  // Normalise to a tables array. New responses carry d.tables[]; old single-table
  // responses carry d.data directly — wrap that so the render loop is always the same.
  const resultTables = (d.tables?.length > 0)
    ? d.tables.filter(tbl => Array.isArray(tbl.data) && tbl.data.length > 0)
    : (Array.isArray(d.data) && d.data.length > 0 ? [{ data: d.data }] : []);

  return (
    <div style={{ display: "flex", gap: "0.6rem", margin: "0.6rem 0", alignItems: "flex-start" }}>
      <Avatar t={t} />
      <div style={{ flex: 1, minWidth: 0 }}>
        {msg.error ? (
          <div style={{ color: "#ef4444", fontSize: "0.9rem" }}>{msg.error}</div>
        ) : (
          <>
            {d.answer && (
              <div style={{ color: t.text, fontSize: "0.92rem", lineHeight: 1.6, marginBottom: "0.5rem" }}>
                {d.answer}
              </div>
            )}
            {d.sql && (
              <Collapsible title="SQL Query" t={t} defaultOpen codeBlock>
                <SqlBlock sql={d.sql} t={t} />
              </Collapsible>
            )}
            {resultTables.map((tbl, i) => (
              <Collapsible
                key={i}
                title={resultTables.length > 1
                  ? `Table ${i + 1} — ${tbl.data.length} rows`
                  : `Results (${tbl.data.length} rows)`}
                t={t}
                defaultOpen
              >
                <ResultTable rows={tbl.data} t={t} />
              </Collapsible>
            ))}
            {d.insights && <Collapsible title="Insights" t={t}>{d.insights}</Collapsible>}
          </>
        )}
      </div>
    </div>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function Avatar({ t }) {
  return (
    <div style={{
      width: 30, height: 30, borderRadius: 8, flexShrink: 0,
      background: `linear-gradient(135deg, ${t.accent}, ${t.accent2})`,
      display: "grid", placeItems: "center", color: "#fff",
    }}>
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
      </svg>
    </div>
  );
}

function Collapsible({ title, children, t, defaultOpen = false, mono = false, codeBlock = false }) {
  const [open, setOpen] = useState(defaultOpen);

  const contentStyle = mono
    ? { padding: "0.5rem 0.75rem", fontFamily: "'JetBrains Mono', monospace", background: t.code, color: t.codeText, whiteSpace: "pre-wrap", overflowX: "auto", fontSize: "0.82rem", lineHeight: 1.5 }
    : codeBlock
    ? { background: t.code }
    : { padding: "0.5rem 0.75rem", fontSize: "0.82rem", color: t.text, lineHeight: 1.5, whiteSpace: "pre-wrap" };

  return (
    <div style={{ border: `1px solid ${t.border}`, borderRadius: 10, marginTop: "0.5rem", overflow: "hidden" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          width: "100%", textAlign: "left", padding: "0.5rem 0.75rem",
          background: "transparent", color: t.textMuted, border: "none",
          cursor: "pointer", fontSize: "0.78rem", fontWeight: 600,
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}
      >
        <span>{title}</span>
        <span style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .15s" }}>▶</span>
      </button>
      {open && (
        <div style={{ borderTop: `1px solid ${t.border}`, ...contentStyle }}>
          {children}
        </div>
      )}
    </div>
  );
}

function ResultTable({ rows, t }) {
  const cols = rows.length ? Object.keys(rows[0]) : [];
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "0.78rem" }}>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} style={{ textAlign: "left", padding: "0.35rem 0.5rem", borderBottom: `1px solid ${t.border}`, color: t.textMuted, fontWeight: 600 }}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 50).map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c} style={{ padding: "0.35rem 0.5rem", borderBottom: `1px solid ${t.border}`, color: t.text }}>
                  {String(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 50 && (
        <div style={{ color: t.textMuted, fontSize: "0.72rem", padding: "0.4rem" }}>
          Showing first 50 of {rows.length} rows
        </div>
      )}
    </div>
  );
}
