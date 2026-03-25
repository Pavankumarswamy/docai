const API_BASE = "http://localhost:8000";
let pollInterval = null;

function switchTab(tab) {
    document.getElementById('tab-local').classList.remove('active');
    document.getElementById('tab-history').classList.remove('active');
    document.getElementById(`tab-${tab}`).classList.add('active');

    document.getElementById('view-local').style.display = 'none';
    document.getElementById('view-history').style.display = 'none';
    document.getElementById(`view-${tab}`).style.display = 'block';

    if (tab === 'history') {
        loadHistory();
    }
}

async function startProcessing() {
    const folderPath = document.getElementById('target-folder').value.trim();
    const excelPath = document.getElementById('target-excel').value.trim();

    if (!folderPath || !excelPath) {
        alert("Please provide both the folder path and the Excel file path.");
        return;
    }

    const runBtn = document.getElementById('run-btn');
    runBtn.disabled = true;
    runBtn.innerHTML = "⌛ Processing...";
    
    document.getElementById('status-area').style.display = 'block';
    document.getElementById('results-area').style.display = 'none';
    document.getElementById('progress-bar').style.width = '10%';
    document.getElementById('status-text').innerText = "Process Initiated...";

    try {
        const payload = {
            repo_url: folderPath,
            excel_file_path: excelPath,
            branch_name: "DOC_UPDATE",
            team_name: "DOCAI",
            leader_name: "PROJECT",
        };

        const response = await fetch(`${API_BASE}/api/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            throw new Error(`Server error: ${response.statusText}`);
        }

        const data = await response.json();
        const runId = data.run_id;
        
        // Start polling for status
        pollStatus(runId);

    } catch (error) {
        document.getElementById('status-text').innerText = `Error: ${error.message}`;
        runBtn.disabled = false;
        runBtn.innerHTML = "▶ Start Document Processing";
    }
}

async function pollStatus(runId) {
    if (pollInterval) clearInterval(pollInterval);
    
    pollInterval = setInterval(async () => {
        try {
            const res = await fetch(`${API_BASE}/api/status/${runId}`);
            if (!res.ok) return;
            const data = await res.json();
            
            const live = data.live || {};
            const phase = live.phase || "processing";
            
            document.getElementById('status-text').innerText = `Current Phase: ${phase.toUpperCase()} - ${live.message || ''}`;
            
            // Advance progress bar artificially based on phase
            const bar = document.getElementById('progress-bar');
            if (phase === 'discovery') bar.style.width = '30%';
            else if (phase === 'execution' || phase === 'fixing') bar.style.width = '70%';
            else if (phase === 'done') bar.style.width = '100%';

            if (data.status === 'completed' || data.status === 'failed' || phase === 'done') {
                clearInterval(pollInterval);
                finishProcessing(runId, data);
            }
        } catch (e) {
            console.error("Polling error", e);
        }
    }, 2000);
}

function finishProcessing(runId, data) {
    const runBtn = document.getElementById('run-btn');
    runBtn.disabled = false;
    runBtn.innerHTML = "▶ Start Document Processing";
    
    document.getElementById('status-area').style.display = 'none';
    document.getElementById('results-area').style.display = 'block';
    
    const resultsContent = document.getElementById('results-content');
    
    // Instead of terminal, we just show a summary of what happened
    const live = data.live || {};
    let summary = `Run ID: ${runId}\nStatus: ${data.status}\n\nFinal Output Log:\n`;
    
    if (live.terminal_output) {
        // Show just the last 1500 chars to avoid overwhelming the view
        summary += live.terminal_output.length > 1500 ? '...' + live.terminal_output.slice(-1500) : live.terminal_output;
    } else {
        summary += "No critical log output found.";
    }
    
    resultsContent.innerText = summary;
}

// Mocking the history function since we removed the DB endpoint for simplicity
async function loadHistory() {
    const hc = document.getElementById('history-content');
    hc.innerHTML = '<p class="text-secondary">Loading history...</p>';
    
    try {
        const res = await fetch(`${API_BASE}/debug/state`);
        if (!res.ok) throw new Error("Could not fetch debug state");
        const data = await res.json();
        
        hc.innerHTML = '';
        if (data.runs_keys && data.runs_keys.length > 0) {
            data.runs_keys.forEach(key => {
                hc.innerHTML += `
                    <div class="history-item">
                        <div class="history-title">Session ID: ${key}</div>
                        <div style="font-size: 0.8rem; margin-top: 4px;">Status: In Memory</div>
                    </div>
                `;
            });
        } else {
            hc.innerHTML = '<p class="text-secondary">No historic runs found in memory.</p>';
        }
    } catch(e) {
        hc.innerHTML = `<p style="color:red">Failed to load history: ${e.message}</p>`;
    }
}
