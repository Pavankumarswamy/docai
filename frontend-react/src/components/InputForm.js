/**
 * InputForm.js – Local Folder, Workspace Name, Owner Name inputs + Run Agent button
 */
import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { useApp } from '../App';
import FolderPickerModal from './FolderPickerModal';

export default function InputForm() {
    const { startRun, loadWorkspace, runState, API_BASE } = useApp();
    const isRunning = runState.status === 'running';

    const [mode, setMode] = useState('local'); // 'local' or 'saved'
    const [savedWorkspaces, setSavedWorkspaces] = useState([]);
    const [loadingSaved, setLoadingSaved] = useState(false);
    const [form, setForm] = useState({
        localPath: '',
        excelPath: '',
        teamName: 'DOC PROCESSING',
        leaderName: 'USER',
    });
    const [isPickerOpen, setIsPickerOpen] = useState(false);

    const fetchWorkspaces = useCallback(async () => {
        setLoadingSaved(true);
        try {
            const { data } = await axios.get(`${API_BASE}/workspaces`);
            setSavedWorkspaces(data.workspaces || []);
        } catch (err) {
            console.error('Failed to fetch workspaces:', err);
        } finally {
            setLoadingSaved(false);
        }
    }, [API_BASE]);

    useEffect(() => {
        if (mode === 'saved') {
            fetchWorkspaces();
        }
    }, [mode, fetchWorkspaces]);

    const handleChange = e => setForm(f => ({ ...f, [e.target.name]: e.target.value }));

    const handleSubmit = e => {
        e.preventDefault();
        if (mode === 'local') {
            if (!form.localPath.trim() || !form.excelPath.trim()) return;
            startRun({ docFolder: form.localPath, excelFile: form.excelPath, teamName: form.teamName, leaderName: form.leaderName });
        }
    };

    return (
        <div className="input-page">
            <div className="input-hero">
                <h1>📄 Document Processing Agent</h1>
                <p className="input-subtitle">
                    Mount a local folder containing your specific Word documents and problem Excel file.
                </p>
            </div>

            <div className="source-toggle card">
                <button className={`toggle-btn ${mode === 'local' ? 'active' : ''}`} onClick={() => setMode('local')}>
                    💻 Local Folder
                </button>
                <button className={`toggle-btn ${mode === 'saved' ? 'active' : ''}`} onClick={() => setMode('saved')}>
                    📁 Saved Workspaces
                </button>
            </div>

            <form className="input-form card" onSubmit={handleSubmit}>
                {mode === 'local' ? (
                    <div className="field-group animate-fade">
                        <label className="field-label">Target Folder Path</label>
                        <div className="input-with-action">
                            <input
                                type="text"
                                name="localPath"
                                placeholder="C:\Users\Name\Documents\MyDocs"
                                value={form.localPath}
                                onChange={handleChange}
                                required
                                disabled={isRunning}
                            />
                            <button
                                type="button"
                                className="action-btn"
                                onClick={() => setIsPickerOpen(true)}
                                disabled={isRunning}
                            >
                                📂 Browse
                            </button>
                        </div>
                        <span className="field-hint">Select the folder holding your .docx files.</span>
                        
                        <label className="field-label" style={{ marginTop: '12px' }}>Target Excel File</label>
                        <div className="input-with-action">
                            <input
                                type="text"
                                name="excelPath"
                                placeholder="C:\Users\Name\Documents\MyDocs\bugs.xlsx"
                                value={form.excelPath}
                                onChange={handleChange}
                                required
                                disabled={isRunning}
                            />
                        </div>
                        <span className="field-hint">Provide the absolute path to your problem context Excel file.</span>
                    </div>
                ) : (
                    <div className="field-group animate-fade">
                        <label className="field-label">📁 Saved Workspaces</label>
                        {loadingSaved ? (
                            <div className="loading-placeholder">🔍 Searching for workspaces…</div>
                        ) : savedWorkspaces.length === 0 ? (
                            <div className="empty-saved">No saved workspaces found. Mount a new folder first!</div>
                        ) : (
                            <div className="saved-list">
                                {savedWorkspaces.map(ws => (
                                    <div key={ws.run_id} className="saved-item card-hover" onClick={() => loadWorkspace(ws.run_id)}>
                                        <div className="saved-item-info">
                                            <div className="saved-title">🏢 {ws.team_name}</div>
                                            <div className="saved-path">{ws.path}</div>
                                        </div>
                                        <div className="saved-actions">
                                            <div className="saved-badge">{ws.status}</div>
                                            <button
                                                className="delete-ws-btn"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    if (window.confirm('Delete this workspace and all its data?')) {
                                                        axios.delete(`${API_BASE}/repos/${ws.run_id}`).then(() => fetchWorkspaces());
                                                    }
                                                }}
                                                title="Delete Workspace"
                                            >
                                                🗑️
                                            </button>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                )}

                <div className="field-row">
                    <div className="field-group">
                        <label className="field-label">Workspace Title</label>
                        <input
                            type="text"
                            name="teamName"
                            placeholder="e.g. DOC PROCESSING"
                            value={form.teamName}
                            onChange={handleChange}
                            disabled={isRunning}
                        />
                    </div>
                    <div className="field-group">
                        <label className="field-label">Owner</label>
                        <input
                            type="text"
                            name="leaderName"
                            placeholder="e.g. USER"
                            value={form.leaderName}
                            onChange={handleChange}
                            disabled={isRunning}
                        />
                    </div>
                </div>

                <div className="branch-preview">
                    <span className="section-label">Session Name</span>
                    <span className="branch-name mono">
                        📂 {form.leaderName || 'Project'}_Dev_Session
                    </span>
                </div>

                {mode !== 'saved' && (
                    <button type="submit" className="btn-primary run-btn" disabled={isRunning || !form.localPath || !form.excelPath}>
                        {isRunning ? (
                            <><span className="spinner" /> Processing Documents…</>
                        ) : (
                            '▶ Start Document Processing'
                        )}
                    </button>
                )}
            </form>

            <FolderPickerModal
                isOpen={isPickerOpen}
                onClose={() => setIsPickerOpen(false)}
                onSelect={(path) => setForm(f => ({ ...f, localPath: path }))}
                API_BASE={API_BASE}
            />

            <div className="info-grid">
                {INFO_CARDS.map(c => (
                    <div key={c.title} className="info-card card card-hover">
                        <div className="info-card-icon">{c.icon}</div>
                        <div>
                            <div className="info-card-title">{c.title}</div>
                            <div className="info-card-desc">{c.desc}</div>
                        </div>
                    </div>
                ))}
            </div>

            <style>{STYLES}</style>
        </div>
    );
}

const INFO_CARDS = [
    { icon: '📂', title: 'Folder Scanning', desc: 'Auto-detects Word docs and Excel files' },
    { icon: '📝', title: 'Content Extraction', desc: 'Reads paragraphs and tables from Word' },
    { icon: '🤖', title: 'LLM-Powered', desc: 'Analyzes problems and generates edits' },
    { icon: '🔁', title: 'Iterative Fixes', desc: 'Applies edits across multiple iterations' },
    { icon: '📊', title: 'Diff Viewing', desc: 'See side-by-side original and modified docs' },
    { icon: '🚀', title: 'Detailed Logging', desc: 'Outputs changes to edits_log.json' },
];

const STYLES = `
  .input-page {
    max-width: 720px;
    margin: 0 auto;
    padding: 32px 24px;
    display: flex;
    flex-direction: column;
    gap: 24px;
    overflow-y: auto;
    height: 100%;
  }
  .input-hero h1 { background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
  .input-subtitle { color: var(--text-secondary); margin-top: 6px; }
  .input-form { display: flex; flex-direction: column; gap: 18px; }
  .field-group { display: flex; flex-direction: column; gap: 6px; flex: 1; }
  .field-label { font-size: 0.78rem; font-weight: 600; letter-spacing: 0.5px; color: var(--text-secondary); text-transform: uppercase; }
  .field-row { display: flex; gap: 16px; }
  .branch-preview { display: flex; align-items: center; gap: 12px; background: var(--bg-primary); padding: 10px 14px; border-radius: var(--radius-sm); border: 1px solid var(--border); }
  .branch-name { color: var(--accent-cyan); font-size: 0.85rem; }
  .run-btn { align-self: flex-start; display: flex; align-items: center; gap: 10px; padding: 12px 32px; font-size: 1rem; }
  .info-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
  .info-card { display: flex; align-items: flex-start; gap: 12px; padding: 14px; }
  .info-card-icon { font-size: 1.4rem; flex-shrink: 0; }
  .info-card-title { font-weight: 600; font-size: 0.88rem; color: var(--text-primary); margin-bottom: 2px; }
  .info-card-desc  { font-size: 0.78rem; color: var(--text-secondary); line-height: 1.5; }

  .source-toggle {
    display: flex;
    gap: 8px;
    padding: 6px;
    background: var(--bg-secondary);
    border-radius: var(--radius-md);
  }
  .toggle-btn {
    flex: 1;
    padding: 10px;
    border-radius: var(--radius-sm);
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--text-secondary);
    background: transparent;
    transition: all 0.2s ease;
  }
  .toggle-btn:hover { background: var(--bg-card); color: var(--text-primary); }
  .toggle-btn.active {
    background: var(--bg-primary);
    color: var(--accent-blue);
    box-shadow: var(--shadow-sm);
  }

  .field-hint { font-size: 0.72rem; color: var(--text-muted); margin-top: 4px; }
  .animate-fade { animation: fadeIn 0.3s ease; }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .input-with-action {
    display: flex;
    gap: 12px;
  }
  .input-with-action input {
    flex: 1;
  }
  .action-btn {
    padding: 0 16px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    color: var(--text-primary);
    font-size: 0.85rem;
    font-weight: 600;
    transition: all 0.2s;
    white-space: nowrap;
  }
  .action-btn:hover {
    background: var(--bg-primary);
    border-color: var(--accent-blue);
    color: var(--accent-blue);
  }

  .saved-list { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; max-height: 300px; overflow-y: auto; padding-right: 4px; }
  .saved-item { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: var(--radius-sm); cursor: pointer; transition: 0.2s; }
  .saved-item:hover { border-color: var(--accent-blue); background: var(--bg-card); }
  .saved-item-info { flex: 1; }
  .saved-title { font-weight: 700; color: var(--text-primary); font-size: 0.9rem; }
  .saved-path { font-size: 0.72rem; color: var(--text-muted); margin-top: 2px; }
  .saved-actions { display: flex; align-items: center; gap: 12px; }
  .saved-badge { font-size: 0.65rem; padding: 2px 8px; border-radius: 10px; background: rgba(255,255,255,0.05); color: var(--text-secondary); text-transform: uppercase; font-weight: 700; }
  .delete-ws-btn { background: transparent; border: none; font-size: 0.9rem; cursor: pointer; opacity: 0.4; transition: 0.2s; }
  .delete-ws-btn:hover { opacity: 1; transform: scale(1.1); }
  .empty-saved { padding: 32px; text-align: center; color: var(--text-muted); font-size: 0.85rem; border: 1px dashed var(--border); border-radius: var(--radius-md); }
  .loading-placeholder { padding: 20px; text-align: center; color: var(--accent-cyan); font-size: 0.85rem; }
`;

