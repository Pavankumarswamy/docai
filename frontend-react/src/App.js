/**
 * App.js – Root component with Context API state management
 *
 * Tabs:
 *   1. Input      – repo URL, team name, leader name + Run button
 *   2. Results    – Summary, Score, Fixes Table, Timeline
 *   3. Code       – Monaco editor with file tree
 */
import React, { createContext, useContext, useState, useEffect, useCallback, useRef, useMemo } from 'react';
import axios from 'axios';

import InputForm from './components/InputForm';
import SummaryCard from './components/SummaryCard';
import ScorePanel from './components/ScorePanel';
import FixesTable from './components/FixesTable';
import Timeline from './components/Timeline';
import CodeEditor from './components/CodeEditor';
import TerminalView from './components/TerminalView';
import SettingsModal from './components/SettingsModal';
import HistoryView from './components/HistoryView';

// ── API base URL ──────────────────────────────────────────────────────────
// In Electron the renderer can talk directly; in browser dev use the proxy.
const API_BASE = (window.electronAPI || window.__ELECTRON__ || navigator.userAgent.includes('Electron'))
    ? 'http://127.0.0.1:8000'
    : '';

// ── Context ───────────────────────────────────────────────────────────────
export const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

// ── Main App ──────────────────────────────────────────────────────────────
export default function App() {
    const [activeTab, setActiveTab] = useState('input');
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);
    const [configStatus, setConfigStatus] = useState({ github_pat_set: true });
    const [runState, setRunState] = useState({
        runId: null,
        status: 'idle',
        branchName: '',
        repoUrl: '',
        teamName: '',
        leaderName: '',
        live: { phase: '', message: '', iterations: [], files: [] },
        result: null,
        error: null,
    });
    const [snippets, setSnippets] = useState([]);
    const [terminalCwd, setTerminalCwd] = useState(null);
    const [terminalLines, setTerminalLines] = useState([{ text: 'Welcome to GGU Terminal. Open a workspace to begin.\n', type: 'system', id: Date.now() }]);
    const [isTerminalRunning, setIsTerminalRunning] = useState(false);

    // ── Lifted Editor State ──────────────────────────────────────────────────
    const [openPaths, setOpenPaths] = useState([]);
    const [activePath, setActivePath] = useState(null);

    const pollRef = useRef(null);
    const terminalWsRef = useRef(null);

    // ── WebSocket Management ──────────────────────────────────────────────────
    const appendTerminalLine = useCallback((text, type = 'output') => {
        setTerminalLines(prev => {
            const newLines = [...prev.slice(-1000), { text, type, id: Math.random().toString(36).substr(2, 9) + Date.now() }];
            return newLines;
        });
    }, []);

    useEffect(() => {
        const runId = runState.runId;
        if (!runId) return;

        // Connect WebSocket
        const WS_BASE = 'ws://127.0.0.1:8000';
        const ws = new WebSocket(`${WS_BASE}/ws/terminal/${runId}`);
        terminalWsRef.current = ws;

        ws.onopen = () => appendTerminalLine('\n🔗 Terminal connected.\n', 'system');
        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === 'output' || msg.type === 'error') {
                    appendTerminalLine(msg.content, msg.type);
                } else if (msg.type === 'cwd') {
                    setTerminalCwd(msg.content);
                } else if (msg.type === 'done') {
                    setIsTerminalRunning(false);
                    appendTerminalLine(`\n[Process exited with code ${msg.exit_code}]\n`, 'system');
                }
            } catch (e) { console.error('WS msg parse error', e); }
        };
        ws.onclose = () => {
            appendTerminalLine('\n⚡ Terminal disconnected.\n', 'system');
            terminalWsRef.current = null;
        };
        ws.onerror = (err) => {
            appendTerminalLine('\n❌ WebSocket error. Reconnect by switching workspace.\n', 'error');
            console.error('WS error', err);
        };

        return () => {
            if (ws) ws.close();
            terminalWsRef.current = null;
        };
    }, [runState.runId, appendTerminalLine]);

    const fetchConfig = useCallback(async () => {
        try {
            const { data } = await axios.get(`${API_BASE}/config`);
            setConfigStatus(data);
        } catch (err) {
            console.error('Failed to fetch config:', err);
        }
    }, []);

    useEffect(() => {
        fetchConfig();
    }, [fetchConfig]);

    // ── Start a run ──────────────────────────────────────────────────────────
    const startRun = useCallback(async ({ docFolder, excelFile, teamName, leaderName }) => {
        try {
            setRunState(s => ({ 
                ...s, 
                status: 'running', 
                repoUrl: docFolder, 
                teamName, 
                leaderName, 
                result: null, 
                error: null,
                live: { phase: 'initializing', message: 'Starting...', iterations: [], files: [] }
            }));
            setActiveTab('results');

            const { data } = await axios.post(`${API_BASE}/analyze`, {
                doc_folder: docFolder,
                excel_file: excelFile,
                team_name: teamName,
                leader_name: leaderName,
            });

            setRunState(s => ({ ...s, runId: data.run_id, branchName: data.branch_name }));

            // Start polling
            pollRef.current = setInterval(async () => {
                try {
                    const { data: poll } = await axios.get(`${API_BASE}/results/${data.run_id}`);
                    setRunState(s => ({
                        ...s,
                        status: poll.status,
                        live: poll.live || s.live,
                        result: poll.result || s.result,
                        branchName: poll.branch_name || s.branchName,
                        error: poll.error || null,
                    }));

                    if (poll.status === 'completed' || poll.status === 'failed') {
                        clearInterval(pollRef.current);
                    }
                } catch (err) {
                    console.error('Poll error:', err);
                    if (err.response && err.response.status === 404) {
                        setRunState(s => ({ ...s, status: 'failed', error: 'Run session lost. The backend might have restarted.' }));
                        clearInterval(pollRef.current);
                    }
                }
            }, 3000);

        } catch (err) {
            setRunState(s => ({ ...s, status: 'failed', error: err?.response?.data?.detail || err.message }));
        }
    }, []);

    const startLocalRun = useCallback(async ({ path, teamName, leaderName }) => {
        try {
            setRunState(s => ({ ...s, status: 'running', repoUrl: `LOCAL: ${path}`, teamName, leaderName, result: null, error: null }));
            const { data } = await axios.post(`${API_BASE}/local/open`, { path, team_name: teamName, leader_name: leaderName });

            setRunState(s => ({
                ...s,
                runId: data.run_id,
                status: 'completed',
                live: { phase: 'done', message: 'Local folder mounted', files: data.files, iterations: [] }
            }));
            setActiveTab('code');
        } catch (err) {
            setRunState(s => ({ ...s, status: 'failed', error: err?.response?.data?.detail || err.message }));
        }
    }, []);

    const downloadFixedCode = useCallback(async () => {
        if (!runState.runId) return;
        try {
            const response = await fetch(`${API_BASE}/download/${runState.runId}`);
            if (!response.ok) {
                const errData = await response.json().catch(() => ({ detail: 'Download service unavailable' }));
                throw new Error(errData.detail || 'Failed to download ZIP');
            }

            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            const fileName = runState.branchName ? `${runState.branchName}.zip` : `fixed_repo_${runState.runId}.zip`;
            link.download = fileName;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            window.URL.revokeObjectURL(url);
        } catch (err) {
            console.error('Download failed:', err);
            alert(`Download failed: ${err.message}`);
        }
    }, [runState.runId, runState.branchName]);

    const loadWorkspace = useCallback(async (runId) => {
        try {
            setRunState(s => ({ ...s, status: 'running', runId, result: null, error: null }));
            const { data: poll } = await axios.get(`${API_BASE}/results/${runId}`);
            setRunState({
                runId: runId,
                status: poll.status,
                branchName: poll.branch_name || '',
                repoUrl: poll.repo_url || '',
                teamName: poll.team_name || '',
                leaderName: poll.leader_name || '',
                live: poll.live || { phase: '', message: '', iterations: [], files: [] },
                result: poll.result || null,
                error: poll.error || null,
            });
            setActiveTab('code');
        } catch (err) {
            console.error('Failed to load workspace:', err);
            setRunState(s => ({ ...s, status: 'failed', error: 'Failed to load workspace.' }));
        }
    }, []);

    const manualSave = useCallback(async () => {
        try {
            await axios.post(`${API_BASE}/save_all`);
            alert('✅ Workspaces saved successfully!');
        } catch (err) {
            console.error('Save failed:', err);
            alert('❌ Failed to save workspaces.');
        }
    }, []);

    const sendTerminalCommand = useCallback(async (command) => {
        if (!command || !runState.runId) return { output: '', error: 'No active workspace.', exit_code: 1, cwd: terminalCwd };
        try {
            const { data } = await axios.post(`${API_BASE}/terminal`, {
                run_id: runState.runId,
                command,
                cwd: terminalCwd || null,
            });
            if (data.cwd) setTerminalCwd(data.cwd);
            // Refresh live state to pick up the new terminal output
            const { data: poll } = await axios.get(`${API_BASE}/results/${runState.runId}`).catch(() => ({ data: null }));
            if (poll) setRunState(s => ({ ...s, live: poll.live || s.live }));
            return data;
        } catch (err) {
            console.error('Terminal command failed:', err);
            return { output: '', error: err.message, exit_code: 1, cwd: terminalCwd };
        }
    }, [runState.runId, terminalCwd]);

    const openFile = useCallback((path) => {
        if (!path) return;
        setOpenPaths(prev => prev.includes(path) ? prev : [...prev, path]);
        setActivePath(path);
        setActiveTab('code');
    }, []);

    const closeFile = useCallback((path) => {
        if (!path) return;
        setOpenPaths(prev => {
            const next = prev.filter(p => p !== path);
            if (activePath === path) {
                setActivePath(next.length > 0 ? next[next.length - 1] : null);
            }
            return next;
        });
    }, [activePath]);

    // Cleanup on unmount
    useEffect(() => () => clearInterval(pollRef.current), []);

    // ── Tabs config ──────────────────────────────────────────────────────────
    const tabs = [
        { id: 'input', label: '⚙️  Input', icon: '⚙️' },
        { id: 'results', label: '📊 Results', icon: '📊' },
        { id: 'code', label: '🖥️  Code', icon: '🖥️' },
        { id: 'history', label: '📦 Repos', icon: '📦' },
    ];

    const ctx = useMemo(() => ({
        runState, setRunState, startRun, startLocalRun, loadWorkspace, manualSave, downloadFixedCode, API_BASE, configStatus, fetchConfig,
        snippets, setSnippets, sendTerminalCommand, terminalCwd,
        terminalLines, terminalWsRef, isTerminalRunning, setIsTerminalRunning,
        openPaths, setOpenPaths, activePath, setActivePath, openFile, closeFile
    }), [runState, startRun, startLocalRun, loadWorkspace, manualSave, downloadFixedCode, configStatus, fetchConfig, snippets, sendTerminalCommand, terminalCwd, terminalLines, isTerminalRunning, openPaths, activePath, openFile, closeFile]);

    return (
        <AppContext.Provider value={ctx}>
            <div className="app-shell">
                {/* ── Header ── */}
                <header className="app-header">
                    <div className="app-header-logo">
                        <span className="logo-icon">⚡</span>
                        <span className="logo-text">GGU AI <span className="logo-year">2026</span></span>
                        <span className="logo-sub">Autonomous Document Processing Agent</span>
                    </div>

                    <nav className="app-tabs">
                        {tabs
                            .filter(t => {
                                if (t.id === 'results' || t.id === 'code') return !!runState.runId;
                                return true;
                            })
                            .map(t => (
                                <button
                                    key={t.id}
                                    className={`tab-btn ${activeTab === t.id ? 'active' : ''}`}
                                    onClick={() => setActiveTab(t.id)}
                                >
                                    {t.label}
                                </button>
                            ))}
                    </nav>

                    <div className="header-status">
                        <button className="tab-btn" onClick={manualSave} style={{ marginRight: 8, background: 'rgba(34, 197, 94, 0.1)', color: '#22c55e' }}>
                            💾 Save Workspace
                        </button>
                        <button className="tab-btn" onClick={() => setIsSettingsOpen(true)} style={{ marginRight: 12 }}>
                            ⚙️ Settings
                        </button>
                        {(runState.status === 'running' || isTerminalRunning) && (
                            <span className="badge badge-blue pulse">
                                <span className="spinner" style={{ width: 10, height: 10 }} />
                                Running
                            </span>
                        )}
                        {runState.status === 'completed' && !isTerminalRunning && (
                            <span className="badge badge-green">✓ Done</span>
                        )}
                        {runState.status === 'failed' && !isTerminalRunning && (
                            <span className="badge badge-red">✗ Error</span>
                        )}
                    </div>
                </header>

                {/* ── Main Content ── */}
                <main className="app-main">
                    {activeTab === 'input' && <InputForm />}
                    {activeTab === 'results' && <ResultsView />}
                    {activeTab === 'code' && <CodeEditor />}
                    {activeTab === 'history' && <HistoryView />}
                </main>

                <SettingsModal isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
            </div>

            {/* ── Inline styles ── */}
            <style>{APP_STYLES}</style>
        </AppContext.Provider>
    );
}

// ── Results composite view ────────────────────────────────────────────────
function ResultsView() {
    const { runState, downloadFixedCode } = useApp();
    const { status, error, live, result } = runState;

    if (status === 'idle') {
        return (
            <div className="empty-state">
                <div className="empty-icon">🚀</div>
                <h2>Ready to Heal</h2>
                <p>Go to the Input tab, fill in your GitHub repo details and click <strong>Run Agent</strong>.</p>
            </div>
        );
    }

    return (
        <div className="results-layout">
            {/* Left column */}
            <div className="results-left scrollable">
                <SummaryCard />
                <ScorePanel />
                {result?.fixes_table?.length > 0 && <FixesTable />}
            </div>
            {/* Right column */}
            <div className="results-right scrollable">
                {error && (
                    <div className="error-banner">
                        <span>⚠️</span> {error}
                    </div>
                )}
                <div className="live-phase card" style={{ marginBottom: 16 }}>
                    <div className="section-label">Live Status</div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <p className={status === 'running' ? 'pulse' : ''} style={{ color: 'var(--accent-cyan)', fontWeight: 600 }}>
                            {live?.phase ? `[${live.phase.toUpperCase()}]` : ''} {live?.message || 'Waiting for backend…'}
                        </p>
                        {status === 'completed' && (
                            <button className="run-btn" style={{ padding: '6px 12px', width: 'auto' }} onClick={downloadFixedCode}>
                                💾 Download Fixed Code
                            </button>
                        )}
                    </div>
                </div>
                <TerminalView />
                <Timeline />
            </div>
        </div>
    );
}

// ── Inline styles ─────────────────────────────────────────────────────────
const APP_STYLES = `
  .app-shell {
    display: flex;
    flex-direction: column;
    height: 100vh;
    background: var(--bg-primary);
  }

  /* Header */
  .app-header {
    display: flex;
    align-items: center;
    gap: 24px;
    padding: 0 24px;
    height: 60px;
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
    z-index: 10;
  }
  .app-header-logo {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }
  .logo-icon { font-size: 1.4rem; }
  .logo-text { font-size: 1.1rem; font-weight: 800; color: var(--accent-blue); }
  .logo-year { color: var(--accent-purple); }
  .logo-sub  { font-size: 0.72rem; color: var(--text-muted); font-weight: 500; letter-spacing: 0.5px; }

  .app-tabs {
    display: flex;
    gap: 4px;
    flex: 1;
    justify-content: center;
  }
  .tab-btn {
    background: transparent;
    color: var(--text-secondary);
    padding: 6px 18px;
    border-radius: var(--radius-sm);
    font-size: 0.85rem;
    font-weight: 500;
  }
  .tab-btn:hover { background: var(--bg-card); color: var(--text-primary); }
  .tab-btn.active {
    background: rgba(79, 142, 247, 0.15);
    color: var(--accent-blue);
    font-weight: 700;
  }
  .header-status { flex-shrink: 0; }

  /* Main */
  .app-main {
    flex: 1;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  /* Results layout */
  .results-layout {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0;
    height: 100%;
    overflow: hidden;
  }
  .results-left, .results-right {
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    height: 100%;
  }
  .results-left  { border-right: 1px solid var(--border); }

  /* Error banner */
  .error-banner {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.3);
    color: var(--accent-red);
    padding: 12px 16px;
    border-radius: var(--radius-md);
    font-weight: 500;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  /* Empty state */
  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    gap: 12px;
    color: var(--text-secondary);
    text-align: center;
  }
  .empty-icon { font-size: 3rem; }
  .empty-state h2 { color: var(--text-primary); }
  .empty-state p  { max-width: 360px; line-height: 1.7; }
`;

