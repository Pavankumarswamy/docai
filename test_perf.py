import time
import requests
import json
import os

payload = {
    "doc_folder": r"c:\Users\shese\Desktop\CICD_AA - Copy\sample\docs",
    "excel_file": r"c:\Users\shese\Desktop\CICD_AA - Copy\sample\bug_list.xlsx",
    "team_name": "DOCAI",
    "leader_name": "PROJECT"
}

print("====================================")
print("DOCAI PERFORMANCE BENCHMARK")
print("====================================")
print(f"Target Folder: {payload['doc_folder']}")
print(f"Target Excel: {payload['excel_file']}")
print("Sending request to local FastAPI backend...")

start_time = time.time()
try:
    res = requests.post("http://localhost:8000/analyze", json=payload)
    if res.status_code != 200:
        print("Failed to start the pipeline. Make sure the backend is running.")
        print(f"Error Code: {res.status_code}")
        print(f"Error Text: {res.text}")
        exit(1)
        
    data = res.json()
    run_id = data.get("run_id")
    print(f"\n[OK] Pipeline initiated successfully.")
    print(f"Run ID: {run_id}\n")
    print("Monitoring Live Status (polling every 5 seconds)...")
    print("-" * 40)
    
    last_phase = ""
    last_message = ""
    
    while True:
        status_res = requests.get(f"http://localhost:8000/results/{run_id}")
        if status_res.status_code == 200:
            status_data = status_res.json()
            status = status_data.get("status", "unknown")
            live = status_data.get("live", {})
            phase = live.get("phase", "N/A")
            message = live.get("message", "N/A")
            
            # Print only when something changes to keep output clean
            if phase != last_phase or message != last_message:
                curr_elapsed = time.time() - start_time
                print(f"[{curr_elapsed:05.1f}s] Phase: {phase.upper():<10} | Message: {message}")
                last_phase = phase
                last_message = message
                
            if status in ['completed', 'failed']:
                print("-" * 40)
                print(f"Pipeline finished with final status: {status.upper()}")
                print(f"Final Live Summary: {message}")
                
                # Try to print some logs if failed
                if status == 'failed':
                    print(f"\nTerminal Output Snippet:\n{live.get('terminal_output', '')[-1500:]}\n")
                break
        else:
            print(f"Warning: Could not fetch status. HTTP {status_res.status_code}")
            
        time.sleep(5)
        
except Exception as e:
    print(f"Exception trying to run API: {e}")

end_time = time.time()
total_time = end_time - start_time

print("====================================")
print(f"TOTAL TIME TAKEN: {total_time:.2f} seconds")
print(f"Time per minute: {total_time/60:.2f} minutes")
print("====================================")
