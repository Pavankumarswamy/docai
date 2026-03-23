import React, { useState, useMemo, useEffect } from 'react';
import axios from 'axios';
import { useApp } from '../App';
import ChatSidebar from './ChatSidebar';
import TerminalView from './TerminalView';

export default function CodeEditor() {
    const {
        runState, API_BASE, loadWorkspace,
        openPaths, activePath, setActivePath, openFile, closeFile
    } = useApp();
    const files = useMemo(() => runState.live?.files || [], [runState.live?.files]);
    const fixes = useMemo(() => runState.result?.fixes_table || [], [runState.result?.fixes_table]);
    const fixedPaths = useMemo(() => new Set(fixes.filter(f => f.status === 'fixed').map(f => f.file)), [fixes]);

    const [diffMode, setDiffMode] = useState(true);
    const [showTerminal, setShowTerminal] = useState(false);
    const terminalHeight = 300;
    const chatWidth = 380;

    const [beforeHtml, setBeforeHtml] = useState('Loading before view...');
    const [afterHtml, setAfterHtml] = useState('Loading after view...');
    
    useEffect(() => {
        if (!activePath || !runState.runId) return;
        
        let isMounted = true;
        
        const fetchHtml = async () => {
            if (activePath.endsWith('.docx')) {
                try {
                    setBeforeHtml('Loading original document...');
                    setAfterHtml('Loading edited document...');
                    const beforeRes = await axios.get(`${API_BASE}/api/document/${runState.runId}/html?type=before&file=${encodeURIComponent(activePath)}`);
                    const afterRes = await axios.get(`${API_BASE}/api/document/${runState.runId}/html?type=after&file=${encodeURIComponent(activePath)}`);
                    if (isMounted) {
                        setBeforeHtml(beforeRes.data.html || 'No document data available.');
                        setAfterHtml(afterRes.data.html || 'No document data available.');
                    }
                } catch (e) {
                    if (isMounted) {
                        setBeforeHtml('Error loading viewer');
                        setAfterHtml('Error loading viewer');
                    }
                }
            } else if (activePath.endsWith('.json') || activePath.endsWith('.xlsx')) {
                try {
                    setBeforeHtml(`Viewing ${activePath}...`);
                    const res = await axios.get(`${API_BASE}/download/${runState.runId}/${activePath}`);
                    const content = typeof res.data === 'string' ? res.data : JSON.stringify(res.data, null, 2);
                    if (isMounted) {
                        setBeforeHtml(`<pre style="white-space: pre-wrap; color: #333;">${content}</pre>`);
                        setAfterHtml(`<pre style="white-space: pre-wrap; color: #333;">${content}</pre>`);
                    }
                } catch (e) {
                    if (isMounted) {
                        setBeforeHtml('Could not load text preview.');
                        setAfterHtml('Could not load text preview.');
                    }
                }
            } else {
                setBeforeHtml(`Preview not supported for ${activePath}`);
                setAfterHtml(`Preview not supported for ${activePath}`);
            }
        };
        fetchHtml();
        
        return () => { isMounted = false; };
    }, [activePath, runState.runId, API_BASE]);

    const closeTab = (e, path) => {
        e.stopPropagation();
        closeFile(path);
    };

    const tree = useMemo(() => {
        const validPaths = files?.filter(f => f && typeof f.path === 'string').map(f => f.path) || [];
        try {
            return buildTree(validPaths);
        } catch (e) {
            console.error('Failed to build file tree:', e);
            return [];
        }
    }, [files]);

    return (
        <div className="code-editor-shell">
            <div className="file-tree scrollable">
                <div className="file-tree-header">
                    <div className="tree-header-left">
                        <span className="section-label">📁 Files</span>
                        <span className="badge badge-gray">{files.length}</span>
                    </div>
                    <div className="tree-header-actions">
                        <button className="icon-btn-tree" title="Refresh Tree" onClick={() => loadWorkspace(runState.runId)}>🔄</button>
                    </div>
                </div>
                {files.length === 0 ? (
                    <p className="tree-empty">Files will appear after processing…</p>
                ) : (
                    <TreeNode
                        nodes={tree}
                        fixedPaths={fixedPaths}
                        selected={activePath}
                        onSelect={openFile}
                    />
                )}
            </div>

            <div className="editor-pane">
                <div className="editor-tab-bar scrollable-x">
                    {openPaths.map(path => (
                        <div
                            key={path}
                            className={`editor-tab ${activePath === path ? 'active' : ''}`}
                            onClick={() => setActivePath(path)}
                        >
                            <span className="tab-icon">{getFileIcon(path.split('/').pop())}</span>
                            <span className="tab-name">{path.split('/').pop()}</span>
                            <button className="tab-close" onClick={(e) => closeTab(e, path)}>×</button>
                        </div>
                    ))}
                </div>

                <div className="editor-toolbar">
                    <div className="editor-path-wrap">
                        <span className="editor-path mono">{activePath || 'Select a document'}</span>
                    </div>
                    {activePath && activePath.endsWith('.docx') && fixedPaths.has(activePath) && (
                        <label className="diff-toggle">
                            <input type="checkbox" checked={diffMode} onChange={e => setDiffMode(e.target.checked)} />
                            <span> Before / After Split</span>
                        </label>
                    )}
                    <button className="icon-btn-tree" title="Toggle Terminal" onClick={() => setShowTerminal(!showTerminal)}>
                        {showTerminal ? '🔽 Term' : '🔼 Term'}
                    </button>
                </div>

                <div className="editor-container">
                    {!activePath ? (
                        <div className="editor-placeholder">
                            <div style={{ fontSize: '2.5rem' }}>📄</div>
                            <p>Select a Word document to view its edits</p>
                        </div>
                    ) : diffMode && activePath.endsWith('.docx') ? (
                        <div className="diff-viewer">
                            <div className="diff-half">
                                <div className="diff-header diff-original">Original Document</div>
                                <div className="diff-content doc-render" dangerouslySetInnerHTML={{ __html: beforeHtml }} />
                            </div>
                            <div className="diff-divider"></div>
                            <div className="diff-half">
                                <div className="diff-header diff-modified">AI Edited Document</div>
                                <div className="diff-content doc-render" dangerouslySetInnerHTML={{ __html: afterHtml }} />
                            </div>
                        </div>
                    ) : (
                        <div className="diff-viewer single-view">
                            <div className="diff-half" style={{ width: '100%' }}>
                                <div className="diff-header">Current Document</div>
                                <div className="diff-content doc-render" dangerouslySetInnerHTML={{ __html: afterHtml }} />
                            </div>
                        </div>
                    )}
                </div>

                {showTerminal && (
                    <div style={{ height: terminalHeight, display: 'flex', flexDirection: 'column', flexShrink: 0, borderTop: '1px solid var(--border)' }}>
                        <div className="terminal-resizer" />
                        <div className="embedded-terminal-wrapper" style={{ flex: 1, minHeight: 0 }}>
                            <TerminalView />
                        </div>
                    </div>
                )}
            </div>

            {runState.runId && (
                <>
                    <div className="chat-resizer-h" />
                    <div className="chat-sidebar-resizable" style={{ width: chatWidth }}>
                        <ChatSidebar currentFile={activePath ? { path: activePath } : null} onFileSelect={openFile} />
                    </div>
                </>
            )}

            <style>{STYLES}</style>
        </div>
    );
}

function buildTree(paths) {
    const root = [];
    const map = {};
    paths.forEach(p => {
        const parts = p.replace(/\\/g, '/').split('/');
        let current = root;
        let accumulated = '';
        parts.filter(Boolean).forEach((part, i) => {
            accumulated = accumulated ? `${accumulated}/${part}` : part;
            const isLast = i === parts.filter(Boolean).length - 1;
            if (!map[accumulated]) {
                const node = { name: part, path: accumulated, children: isLast ? undefined : [] };
                map[accumulated] = node;
                current.push(node);
            } else if (!isLast && !map[accumulated].children) {
                // Was previously a leaf, but now it's a parent
                map[accumulated].children = [];
            }
            if (!isLast) current = map[accumulated].children;
        });
    });
    const sortNodes = (nodes) => {
        nodes.sort((a, b) => {
            const aIsDir = !!a.children;
            const bIsDir = !!b.children;
            if (aIsDir && !bIsDir) return -1;
            if (!aIsDir && bIsDir) return 1;
            return a.name.localeCompare(b.name);
        });
        nodes.forEach(n => { if (n.children) sortNodes(n.children); });
    };
    sortNodes(root);
    return root;
}

function getFileIcon(name) {
    const ext = name.split('.').pop();
    return { docx: '📄', xlsx: '📊', json: '🗂' }[ext] || '📄';
}

const STYLES = `
  .code-editor-shell { display: flex; height: 100%; overflow: hidden; }
  .file-tree { width: 240px; flex-shrink: 0; border-right: 1px solid var(--border); background: var(--bg-secondary); display: flex; flex-direction: column; }
  .file-tree-header { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border-bottom: 1px solid var(--border); }
  .tree-empty { padding: 16px 12px; color: var(--text-muted); font-size: 0.82rem; }
  .tree-item { display: flex; align-items: center; gap: 6px; padding: 5px 10px; cursor: pointer; border-radius: 4px; font-size: 0.82rem; transition: var(--transition); }
  .tree-item:hover { background: var(--bg-card); }
  .tree-item-selected { background: rgba(79,142,247,0.15) !important; color: var(--accent-blue); }
  .tree-item-fixed .tree-name { color: var(--accent-green); }
  .tree-icon { font-size: 0.9rem; flex-shrink: 0; }
  .tree-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #eee; font-weight: 500; }
  .tree-header-left { display: flex; align-items: center; gap: 8px; }
  .tree-header-actions { display: flex; gap: 4px; }
  .icon-btn-tree { background: transparent; padding: 2px 6px; border-radius: 4px; color: var(--text-muted); font-size: 0.8rem; border: 1px solid transparent; }
  .icon-btn-tree:hover { background: var(--border); color: var(--text-primary); border-color: var(--border-bright); }
  .tree-children { margin-left: 14px; border-left: 1px solid var(--border-muted); position: relative; }
  .tree-item { position: relative; transition: 0.2s; }
  .tree-item-nested::before { content: ''; position: absolute; left: -14px; top: 12px; width: 10px; height: 1px; background: var(--border-muted); }
  .editor-pane { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .editor-toolbar { display: flex; align-items: center; justify-content: space-between; padding: 8px 14px; background: var(--bg-secondary); border-bottom: 1px solid var(--border); flex-shrink: 0; }
  .editor-path { font-size: 0.75rem; color: var(--text-muted); opacity: 0.7; }
  .editor-path-wrap { display: flex; align-items: center; gap: 12px; }
  .diff-toggle { display: flex; align-items: center; gap: 6px; color: var(--text-secondary); font-size: 0.8rem; cursor: pointer; }
  .editor-placeholder { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; color: var(--text-muted); }
  .editor-tab-bar { display: flex; background: var(--bg-card); border-bottom: 1px solid var(--border); overflow-x: auto; scrollbar-width: none; flex-shrink: 0; }
  .editor-tab-bar::-webkit-scrollbar { display: none; }
  .editor-tab { display: flex; align-items: center; gap: 8px; padding: 0 14px; height: 36px; border-right: 1px solid var(--border); cursor: pointer; color: var(--text-muted); font-size: 0.78rem; font-weight: 500; transition: 0.2s; position: relative; min-width: 120px; max-width: 200px; }
  .editor-tab:hover { background: rgba(255,255,255,0.03); color: var(--text-primary); }
  .editor-tab.active { background: var(--bg-secondary); color: var(--accent-blue); }
  .editor-tab.active::after { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: var(--accent-blue); }
  .tab-icon { font-size: 0.9rem; opacity: 0.8; }
  .tab-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tab-close { background: transparent; border: none; color: var(--text-muted); font-size: 1.1rem; line-height: 1; padding: 2px 4px; border-radius: 4px; opacity: 0; transition: 0.2s; }
  .editor-tab:hover .tab-close { opacity: 1; }
  .tab-close:hover { background: rgba(255,255,255,0.1); color: #fff; }
  .chat-resizer-h { width: 4px; cursor: col-resize; background: var(--border); transition: 0.2s; flex-shrink: 0; z-index: 10; }
  .chat-sidebar-resizable { display: flex; flex-direction: column; flex-shrink: 0; }
  .editor-container { flex: 1; position: relative; overflow: hidden; background: #fff; color: #000; display: flex;}
  .terminal-resizer { height: 4px; background: var(--border); cursor: ns-resize; transition: 0.2s; flex-shrink: 0; z-index: 10; }
  
  .diff-viewer { display: flex; width: 100%; height: 100%; overflow: hidden; }
  .diff-half { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .diff-divider { width: 1px; background: #ccc; z-index: 5; }
  .diff-header { padding: 4px 12px; background: #f0f0f0; border-bottom: 1px solid #ccc; font-weight: bold; font-size: 13px; text-align: center; }
  .diff-original { color: #555; }
  .diff-modified { color: #0056b3; }
  .diff-content { flex: 1; overflow: auto; padding: 24px; }
  .doc-render { font-family: "Calibri", "Arial", sans-serif; font-size: 14px; line-height: 1.6; color: #333; }
  .doc-render table { border-collapse: collapse; width: 100%; margin: 12px 0; }
  .doc-render td, .doc-render th { border: 1px solid #aaa; padding: 6px 8px; }
  .doc-render p { margin-bottom: 8px; }
`;

function TreeNode({ nodes, fixedPaths, selected, onSelect, depth = 0 }) {
    if (!Array.isArray(nodes)) return null;
    return (
        <div>
            {nodes.map(node => (
                <TreeItem
                    key={node.path}
                    node={node}
                    fixedPaths={fixedPaths}
                    selected={selected}
                    onSelect={onSelect}
                    depth={depth}
                />
            ))}
        </div>
    );
}

function TreeItem({ node, fixedPaths, selected, onSelect, depth }) {
    const [open, setOpen] = useState(false);
    if (!node) return null;
    const isDir = !!node.children;
    const isFixed = !isDir && fixedPaths.has(node.path);
    const isSel = selected === node.path;
    return (
        <div>
            <div
                className={`tree-item ${isSel ? 'tree-item-selected' : ''} ${isFixed ? 'tree-item-fixed' : ''} ${depth > 0 ? 'tree-item-nested' : ''}`}
                style={{ paddingLeft: 10 }}
                onClick={() => isDir ? setOpen(o => !o) : onSelect(node.path)}
            >
                <span className="tree-icon">{isDir ? (open ? '📂' : '📁') : getFileIcon(node.name)}</span>
                <span className="tree-name">{node.name}</span>
                {isFixed && <span className="badge badge-green" style={{ fontSize: '0.65rem', padding: '1px 5px', marginLeft: 'auto' }}>Fixed</span>}
            </div>
            {isDir && open && (
                <div className="tree-children">
                    <TreeNode
                        nodes={node.children}
                        fixedPaths={fixedPaths}
                        selected={selected}
                        onSelect={onSelect}
                        depth={depth + 1}
                    />
                </div>
            )}
        </div>
    );
}

