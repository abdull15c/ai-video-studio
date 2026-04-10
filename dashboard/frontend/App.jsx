const { useState, useEffect, useCallback, useMemo } = React;

const STEP_LABELS = {
  script: "Сценарий",
  voice: "Голос",
  videos: "Видео",
  subtitles: "Субтитры",
  montage: "Монтаж",
  audio: "Микс",
  seo: "SEO",
};

function formatBytes(n) {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i > 0 ? 1 : 0)} ${u[i]}`;
}

function formatDur(sec) {
  if (sec == null) return "—";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function StatusBadge({ ui }) {
  const colors = {
    completed: "#2e7d32",
    failed: "#c62828",
    processing: "#f9a825",
    queued: "#5c6bc0",
    pending: "#78909c",
  };
  const c = colors[ui] || "#78909c";
  return (
    <span
      style={{
        background: c,
        color: "#fff",
        padding: "2px 8px",
        borderRadius: 6,
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      {ui}
    </span>
  );
}

function FormatBadge({ format }) {
  const c = { short: "#8e24aa", main: "#1565c0", long: "#6a1b9a" }[format] || "#555";
  return (
    <span style={{ background: c, color: "#fff", padding: "2px 6px", borderRadius: 4, fontSize: 11 }}>
      {format}
    </span>
  );
}

function ProgressDots({ progress, uiStatus }) {
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
      {(progress || []).map((p) => {
        let bg = "#333";
        let cls = "";
        if (p.state === "done") bg = "#2e7d32";
        else if (p.state === "error") bg = "#c62828";
        else if (p.state === "current") {
          bg = "#f9a825";
          cls = "pulse";
        }
        return (
          <div
            key={p.step}
            title={STEP_LABELS[p.step] || p.step}
            className={cls}
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: bg,
            }}
          />
        );
      })}
    </div>
  );
}

function App() {
  const [stats, setStats] = useState(null);
  const [projects, setProjects] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState("all");
  const [formatFilter, setFormatFilter] = useState("");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState(null);
  const [detail, setDetail] = useState(null);
  const [logs, setLogs] = useState([]);
  const [queue, setQueue] = useState([]);
  const [voices, setVoices] = useState([]);
  const [newTopic, setNewTopic] = useState("");
  const [queueDraft, setQueueDraft] = useState("");
  const [newFormat, setNewFormat] = useState("short");
  const [newPreset, setNewPreset] = useState("1 hour");
  const [newVoice, setNewVoice] = useState("");
  const [dragFrom, setDragFrom] = useState(null);

  const hasProcessing = useMemo(
    () => projects.some((p) => p.ui_status === "processing"),
    [projects]
  );

  const loadStats = useCallback(async () => {
    try {
      const r = await fetch("/api/stats");
      setStats(await r.json());
    } catch (e) {
      console.error(e);
    }
  }, []);

  const loadProjects = useCallback(async () => {
    try {
      const params = new URLSearchParams({
        page: String(page),
        page_size: "20",
        status: statusFilter,
        q: search,
      });
      if (formatFilter) params.set("format_filter", formatFilter);
      const r = await fetch(`/api/projects?${params}`);
      const data = await r.json();
      setProjects(data.items || []);
      setTotal(data.total || 0);
    } catch (e) {
      console.error(e);
    }
  }, [page, statusFilter, formatFilter, search]);

  const loadQueue = useCallback(async () => {
    try {
      const r = await fetch("/api/queue");
      const d = await r.json();
      setQueue(d.topics || []);
    } catch (e) {
      console.error(e);
    }
  }, []);

  const loadVoices = useCallback(async () => {
    try {
      const r = await fetch("/api/voices");
      const d = await r.json();
      setVoices(d.voices || []);
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    loadVoices();
    loadQueue();
  }, [loadVoices, loadQueue]);

  useEffect(() => {
    loadStats();
    loadProjects();
  }, [loadStats, loadProjects]);

  useEffect(() => {
    const ms = hasProcessing ? 5000 : 10000;
    const t = setInterval(() => {
      loadStats();
      loadProjects();
    }, ms);
    return () => clearInterval(t);
  }, [hasProcessing, loadStats, loadProjects]);

  const toggleExpand = async (id) => {
    if (expanded === id) {
      setExpanded(null);
      setDetail(null);
      setLogs([]);
      return;
    }
    setExpanded(id);
    try {
      const [dr, lr] = await Promise.all([
        fetch(`/api/projects/${id}`).then((r) => r.json()),
        fetch(`/api/projects/${id}/logs?tail=200`).then((r) => r.json()),
      ]);
      setDetail(dr);
      setLogs(lr.lines || []);
    } catch (e) {
      console.error(e);
    }
  };

  const uploadAvatar = async (projectId, file) => {
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    try {
      const r = await fetch(`/api/projects/${projectId}/avatar`, {
        method: "POST",
        body: formData,
      });
      if (!r.ok) throw new Error(await r.text());
      alert("Аватар успешно загружен!");
      // Обновляем детали проекта
      const dr = await fetch(`/api/projects/${projectId}`).then((r) => r.json());
      setDetail(dr);
    } catch (e) {
      alert("Ошибка при загрузке аватара: " + e.message);
    }
  };

  const submitNew = async (e) => {
    e.preventDefault();
    if (!newTopic.trim()) return;
    try {
      const r = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          topic: newTopic.trim(),
          format: newFormat,
          voice: newVoice || null,
          preset: newFormat === "long" ? newPreset : "default",
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      setNewTopic("");
      loadProjects();
      loadStats();
    } catch (e) {
      alert("Ошибка запуска: " + e.message);
    }
  };

  const addQueueLine = async (e) => {
    e.preventDefault();
    const t = queueDraft.trim();
    if (!t) return;
    await fetch("/api/queue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic: t }),
    });
    setQueueDraft("");
    loadQueue();
  };

  const removeQueue = async (idx) => {
    await fetch(`/api/queue/${idx}`, { method: "DELETE" });
    loadQueue();
  };

  const onDropQueue = async (dropIdx) => {
    if (dragFrom === null || dragFrom === dropIdx) {
      setDragFrom(null);
      return;
    }
    const n = queue.length;
    const perm = Array.from({ length: n }, (_, i) => i);
    const [x] = perm.splice(dragFrom, 1);
    perm.splice(dropIdx, 0, x);
    await fetch("/api/queue", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order: perm }),
    });
    setDragFrom(null);
    loadQueue();
  };

  const cardStyle = {
    background: "#1a1d26",
    borderRadius: 12,
    padding: "16px 20px",
    border: "1px solid #2a2f3d",
    minWidth: 120,
  };

  return (
    <div style={{ maxWidth: 1400, margin: "0 auto", padding: 24 }}>
      <h1 style={{ marginTop: 0, fontWeight: 700 }}>AI Video Studio</h1>

      {/* Статистика */}
      <section style={{ display: "flex", flexWrap: "wrap", gap: 16, marginBottom: 28, alignItems: "stretch" }}>
        {[
          ["Всего", stats?.total_projects],
          ["Завершено", stats?.completed],
          ["В работе", stats?.in_progress],
          ["Ошибки", stats?.failed],
        ].map(([label, val]) => (
          <div key={label} style={cardStyle}>
            <div style={{ fontSize: 13, color: "#9aa0a6" }}>{label}</div>
            <div style={{ fontSize: 28, fontWeight: 700 }}>{val ?? "—"}</div>
          </div>
        ))}
        <div style={{ ...cardStyle, flex: 1, minWidth: 200 }}>
          <div style={{ fontSize: 13, color: "#9aa0a6" }}>Диск (свободно)</div>
          <div style={{ fontSize: 18, fontWeight: 600 }}>
            {stats ? formatBytes(stats.disk_free_bytes) : "—"} / {stats ? formatBytes(stats.disk_total_bytes) : "—"}
          </div>
        </div>
        <div style={{ ...cardStyle, minWidth: 220 }}>
          <div style={{ fontSize: 13, color: "#9aa0a6" }}>Автопилот</div>
          <div style={{ fontSize: 16, fontWeight: 600, color: stats?.autopilot_running ? "#66bb6a" : "#ef5350" }}>
            {stats?.autopilot_running ? "Активен" : "Не запущен"}
          </div>
          {stats?.autopilot_next_run_iso && (
            <div style={{ fontSize: 12, color: "#9aa0a6", marginTop: 4 }}>След. запуск: {stats.autopilot_next_run_iso}</div>
          )}
          {stats?.autopilot_schedule_label && (
            <div style={{ fontSize: 11, color: "#666" }}>{stats.autopilot_schedule_label}</div>
          )}
        </div>
      </section>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 24, alignItems: "start" }}>
        {/* Таблица */}
        <section style={{ background: "#1a1d26", borderRadius: 12, border: "1px solid #2a2f3d", overflow: "hidden" }}>
          <div style={{ padding: 16, borderBottom: "1px solid #2a2f3d", display: "flex", flexWrap: "wrap", gap: 12 }}>
            <select value={statusFilter} onChange={(e) => { setPage(1); setStatusFilter(e.target.value); }} style={sel}>
              <option value="all">Все статусы</option>
              <option value="completed">Завершённые</option>
              <option value="processing">В работе</option>
              <option value="failed">Ошибки</option>
              <option value="queued">В очереди</option>
              <option value="pending">Ожидание (CLI)</option>
            </select>
            <select value={formatFilter} onChange={(e) => { setPage(1); setFormatFilter(e.target.value); }} style={sel}>
              <option value="">Все форматы</option>
              <option value="short">short</option>
              <option value="main">main</option>
              <option value="long">long</option>
            </select>
            <input
              placeholder="Поиск по названию"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ ...inp, flex: 1, minWidth: 160 }}
            />
            <button type="button" onClick={() => { setPage(1); loadProjects(); }} style={btn}>
              Обновить
            </button>
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "#9aa0a6", borderBottom: "1px solid #2a2f3d" }}>
                <th style={th}>Название</th>
                <th style={th}>Формат</th>
                <th style={th}>Голос</th>
                <th style={th}>Дата</th>
                <th style={th}>Статус</th>
                <th style={th}>Прогресс</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <React.Fragment key={p.id}>
                  <tr
                    onClick={() => toggleExpand(p.id)}
                    style={{
                      cursor: "pointer",
                      borderBottom: "1px solid #2a2f3d",
                      background: expanded === p.id ? "#222632" : "transparent",
                      outline: p.ui_status === "failed" ? "1px solid #c62828" : "none",
                    }}
                  >
                    <td style={td}>{p.title}</td>
                    <td style={td}>
                      <FormatBadge format={p.format} />
                    </td>
                    <td style={{ ...td, fontSize: 12, color: "#9aa0a6" }}>{p.tts_voice || "—"}</td>
                    <td style={{ ...td, fontSize: 12, color: "#9aa0a6" }}>{p.created_at?.replace("T", " ").slice(0, 19) || "—"}</td>
                    <td style={td}>
                      <StatusBadge ui={p.ui_status} />
                    </td>
                    <td style={td}>
                      <ProgressDots progress={p.progress} uiStatus={p.ui_status} />
                    </td>
                  </tr>
                  {expanded === p.id && detail && detail.id === p.id && (
                    <tr>
                      <td colSpan={6} style={{ padding: 16, background: "#13151c", verticalAlign: "top" }}>
                        {detail.error_message && (
                          <div style={{ color: "#ef5350", marginBottom: 12, whiteSpace: "pre-wrap" }}>
                            <strong>Ошибка:</strong> {detail.error_message}
                          </div>
                        )}
                        <div style={{ marginBottom: 12, display: "flex", gap: 12, flexWrap: "wrap" }}>
                          <a href={detail.download_url} download style={{ ...btn, textDecoration: "none", display: "inline-block" }}>
                            Скачать видео
                          </a>
                          {detail.thumbnail_url && (
                            <a href={detail.thumbnail_url} download style={{ ...btn, textDecoration: "none", display: "inline-block" }}>
                              Скачать превью
                            </a>
                          )}
                          <span style={{ color: "#9aa0a6", fontSize: 13, alignSelf: "center" }}>
                            Размер: {formatBytes(detail.final_video_size_bytes)} · Длительность: {formatDur(detail.final_video_duration_sec)}
                            {detail.llm_usage_estimated_usd != null && detail.llm_usage_estimated_usd > 0 && (
                              <> · LLM ~${Number(detail.llm_usage_estimated_usd).toFixed(4)}</>
                            )}
                          </span>
                        </div>

                        {/* Блок управления аватаром (Talking Head) */}
                        <div style={{ background: "#1a1d26", padding: 12, borderRadius: 8, marginBottom: 16, border: "1px solid #2a2f3d" }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                            <div>
                              <strong style={{ display: "block", marginBottom: 4 }}>Аватар (Talking Head)</strong>
                              <span style={{ fontSize: 12, color: "#9aa0a6" }}>
                                {detail.avatar_type === "none" || !detail.avatar_type 
                                  ? "Не установлен" 
                                  : `Установлен (${detail.avatar_type.toUpperCase()}) - будет добавлен при монтаже`}
                              </span>
                            </div>
                            <div style={{ display: "flex", gap: 8 }}>
                              <label style={{ ...btnSm, cursor: "pointer", background: "#4caf50" }}>
                                Загрузить PNG / MP4
                                <input 
                                  type="file" 
                                  accept=".png,.jpg,.jpeg,.mp4" 
                                  style={{ display: "none" }}
                                  onChange={(e) => uploadAvatar(detail.id, e.target.files[0])}
                                />
                              </label>
                            </div>
                          </div>
                          <div style={{ fontSize: 11, color: "#78909c", marginTop: 6 }}>
                            * PNG для статичного (дышащего) аватара. MP4 для анимированного (моргание). Аватар должен иметь прозрачный фон или хромакей. 
                            Загружайте аватар до начала этапа монтажа!
                          </div>
                        </div>

                        <div style={{ fontWeight: 600, marginBottom: 12, marginTop: 12 }}>Предпросмотр: Раскадровка (Storyboard)</div>
                        <div style={{ 
                          maxHeight: 400, 
                          overflowY: "auto", 
                          fontSize: 13, 
                          marginBottom: 16,
                          display: "flex",
                          flexDirection: "column",
                          gap: "12px"
                        }}>
                          {detail.chapters?.map((ch) => (
                            <div key={ch.id} style={{ background: "#222632", borderRadius: 8, padding: 12, border: "1px solid #2a2f3d" }}>
                              <strong style={{ display: "block", marginBottom: 12, fontSize: 14, color: "#fff" }}>
                                Глава {ch.number}. {ch.title}
                              </strong>
                              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
                                {ch.scenes?.map((s) => (
                                  <div key={s.id} style={{ 
                                    background: "#13151c", 
                                    borderRadius: 6, 
                                    padding: 10,
                                    borderLeft: "3px solid #3949ab"
                                  }}>
                                    <div style={{ fontWeight: 600, marginBottom: 6, color: "#82b1ff" }}>Сцена {s.number}</div>
                                    
                                    <div style={{ marginBottom: 8 }}>
                                      <span style={{ color: "#9aa0a6", fontSize: 11, textTransform: "uppercase" }}>Текст диктора:</span>
                                      <div style={{ fontStyle: "italic", marginTop: 2, lineHeight: 1.4 }}>"{s.narration}"</div>
                                    </div>
                                    
                                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, fontSize: 11 }}>
                                      {s.camera && <span style={{ background: "#333", padding: "2px 6px", borderRadius: 4 }}>🎥 {s.camera}</span>}
                                      {s.mood && <span style={{ background: "#333", padding: "2px 6px", borderRadius: 4 }}>🎭 {s.mood}</span>}
                                      {s.duration_sec && <span style={{ background: "#333", padding: "2px 6px", borderRadius: 4 }}>⏱ ~{s.duration_sec}s</span>}
                                    </div>
                                    
                                    {s.image_prompt && (
                                      <div style={{ marginTop: 8, fontSize: 11, color: "#b0bec5", background: "#0a0c10", padding: "4px 8px", borderRadius: 4 }}>
                                        <strong>Визуал:</strong> {s.image_prompt}
                                      </div>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>

                        <div style={{ fontWeight: 600, marginBottom: 8 }}>Лог (pipeline)</div>
                        <pre
                          style={{
                            background: "#0a0c10",
                            padding: 12,
                            borderRadius: 8,
                            maxHeight: 200,
                            overflow: "auto",
                            fontSize: 11,
                            color: "#b0bec5",
                          }}
                        >
                          {logs.length ? logs.join("\n") : "Нет строк с project_id в pipeline.log"}
                        </pre>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
          <div style={{ padding: 12, display: "flex", justifyContent: "space-between", color: "#9aa0a6" }}>
            <span>
              Стр. {page} · Всего {total}
            </span>
            <span>
              <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} style={btnSm}>
                Назад
              </button>
              <button
                type="button"
                disabled={page * 20 >= total}
                onClick={() => setPage((p) => p + 1)}
                style={{ ...btnSm, marginLeft: 8 }}
              >
                Вперёд
              </button>
            </span>
          </div>
        </section>

        {/* Панель */}
        <aside style={{ background: "#1a1d26", borderRadius: 12, border: "1px solid #2a2f3d", padding: 16 }}>
          <h3 style={{ marginTop: 0 }}>Новый проект</h3>
          <form onSubmit={submitNew}>
            <label style={lbl}>Тема</label>
            <input value={newTopic} onChange={(e) => setNewTopic(e.target.value)} style={{ ...inp, width: "100%" }} required />
            <label style={lbl}>Формат</label>
            <select value={newFormat} onChange={(e) => setNewFormat(e.target.value)} style={{ ...inp, width: "100%" }}>
              <option value="short">short</option>
              <option value="main">main</option>
              <option value="long">long</option>
            </select>
            
            {newFormat === "long" && (
              <>
                <label style={lbl}>Длительность (Long)</label>
                <select value={newPreset} onChange={(e) => setNewPreset(e.target.value)} style={{ ...inp, width: "100%" }}>
                  <option value="1 hour">До 1 часа</option>
                  <option value="2 hours">До 2 часов</option>
                </select>
              </>
            )}

            <label style={lbl}>Голос (опционально)</label>
            <select value={newVoice} onChange={(e) => setNewVoice(e.target.value)} style={{ ...inp, width: "100%" }}>
              <option value="">— LLM / по умолчанию —</option>
              {voices.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.id}
                </option>
              ))}
            </select>
            <button type="submit" style={{ ...btn, width: "100%", marginTop: 12 }}>
              Запустить
            </button>
          </form>

          <h3 style={{ marginTop: 24 }}>Очередь тем</h3>
          <form onSubmit={addQueueLine} style={{ marginBottom: 12 }}>
            <input
              placeholder="Добавить в topics.txt"
              value={queueDraft}
              onChange={(e) => setQueueDraft(e.target.value)}
              style={{ ...inp, width: "100%" }}
            />
            <button type="submit" style={{ ...btnSm, marginTop: 8, width: "100%" }}>
              В очередь
            </button>
          </form>
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {queue.map((q) => (
              <li
                key={q.index}
                draggable
                onDragStart={() => setDragFrom(q.index)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => onDropQueue(q.index)}
                style={{
                  padding: "8px 10px",
                  marginBottom: 6,
                  background: "#13151c",
                  borderRadius: 8,
                  cursor: "grab",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <span style={{ fontSize: 13 }}>{q.text}</span>
                <button type="button" onClick={() => removeQueue(q.index)} style={btnXs}>
                  ×
                </button>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </div>
  );
}

const sel = { background: "#13151c", color: "#e8eaed", border: "1px solid #333", borderRadius: 8, padding: "8px 10px" };
const inp = { background: "#13151c", color: "#e8eaed", border: "1px solid #333", borderRadius: 8, padding: "8px 10px" };
const btn = { background: "#3949ab", color: "#fff", border: "none", borderRadius: 8, padding: "10px 16px", cursor: "pointer", fontWeight: 600 };
const btnSm = { ...btn, padding: "6px 12px", fontSize: 13 };
const btnXs = { background: "#444", color: "#fff", border: "none", borderRadius: 4, width: 28, height: 28, cursor: "pointer" };
const th = { padding: "12px 14px" };
const td = { padding: "12px 14px", verticalAlign: "middle" };
const lbl = { display: "block", fontSize: 12, color: "#9aa0a6", marginTop: 10, marginBottom: 4 };

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
