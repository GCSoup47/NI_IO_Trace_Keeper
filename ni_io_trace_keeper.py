import os
import time
import socket
import threading
import subprocess
from datetime import datetime

import psutil
import pyvisa
from pywinauto import Application


class NIIOTraceKeeper:
    def __init__(self):
        # NI I/O Trace application settings
        self.PROCESS_NAME = "NI IO Trace.exe"
        self.EXE_PATH = r"C:\Program Files (x86)\National Instruments\NI IO Trace\NI IO Trace.exe"
        self.LOG_FILE = r"C:\Users\TR010153\OneDrive - SubCom, LLC\Documents\Python Projects\NI_Trace_Helper\ni_io_trace_keeper.log"

        # Watchdog timing settings in seconds
        self.CHECK_INTERVAL = 1
        self.RESTART_DELAY = 5
        self.POST_LAUNCH_WAIT = 3 #Wait 3 seconds before automating UI.

        # NI-VISA settings use this instead of pyvisa-py. pyvisa-py not compatible with NI I/O Trace's VISA usage.
        self.VISA_DLL = r"C:\Windows\System32\visa64.dll"

        # Embedded marker server / anchor settings
        self.ANCHOR_HOST = "127.0.0.1" #Establish "ghost" server that will listen on localhose, port 5025
        self.ANCHOR_PORT = 5025
        self.ANCHOR_RESOURCE = f"TCPIP0::{self.ANCHOR_HOST}::{self.ANCHOR_PORT}::SOCKET" #VISA resource string sent to NI-VISA
        self.ANCHOR_INTERVAL_SECONDS = 300 #Heartbeat anchor every 5 mins
        self.ANCHOR_OPEN_TIMEOUT_MS = 2000 #Timout when opening socket resource with NI-VISA. 
        self.ANCHOR_WRITE_TERMINATION = "\n" #Append newline when writing the anchor

        # Internal state
        self.last_anchor_time = 0.0 
        self.server_ready_event = threading.Event() #Tells main thread whether background marker server finished startup.
        self.stop_server_event = threading.Event() #Gives a way to tell the server thread to stop
        self.server_thread = None
        self.server_ok = False

    #Logs timestamped messages to both console and log file.
    def log(self, msg): 
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line) #Print to console
        with open(self.LOG_FILE, "a", encoding="utf-8") as f: #Write to log file
            f.write(line + "\n")

    # Embedded marker server
    def marker_server_worker(self):
        #Write to log that the server has started. 
        self.log("Embedded marker server worker starting...")
        #Try to create the server TCP/IP socket. AF_INET = IPv4, SOCK_STREAM = TCP. Allows rebinding port quickly after restart with SO_REUSEADDR.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

                self.log(f"Embedded marker server binding to {self.ANCHOR_HOST}:{self.ANCHOR_PORT}...")
                server.bind((self.ANCHOR_HOST, self.ANCHOR_PORT)) #Bind to localhost:5025.

                server.listen(5) #start listening for TCP connections. 5 is the backlog param allowed meaning that it will queue up to 5 incoming connections before accept() handles them.
                server.settimeout(0.5) #Makes accept() wait .5 seconds before timeout so the loop can check whether it should stop.

                self.server_ok = True #Server healthy
                self.server_ready_event.set() #Signals main thread that startup is complete.
                self.log(f"Embedded marker server listening on {self.ANCHOR_HOST}:{self.ANCHOR_PORT}")
                #Accept loop
                while not self.stop_server_event.is_set():
                    try:
                        #Keep accepting clients until told otherwise.
                        conn, addr = server.accept()
                    except socket.timeout:
                        continue #Loop again if no connections accepted within settimeout. 
                    except OSError as e:
                        self.log(f"Embedded marker server accept stopped: {e}") #Stop if somehting goes wrong. 
                        break
                    
                    self.log(f"Embedded marker server accepted connection from {addr}")
                    #Read from connected client.
                    with conn:
                        while not self.stop_server_event.is_set():
                            try:
                                data = conn.recv(4096) #Read up to 4096 bytes from the client. This will block until data is received or the connection is closed.
                            except ConnectionResetError: #Client force closed connection
                                self.log(f"Embedded marker server connection reset by {addr}")
                                break
                            except OSError as e: #Socket failure
                                self.log(f"Embedded marker server recv stopped: {e}")
                                break

                            if not data: #Empty bytes means client closed cleanly. 
                                self.log(f"Embedded marker server client disconnected: {addr}")
                                break

                            decoded = data.decode(errors="ignore") #Decode bytes to text and log payload. 
                            self.log(f"Embedded marker server received: {decoded!r}")

        except Exception as e: #If the whole thing crashes, mark server as dead, log why, and set ready event so main thread can continue. 
            self.server_ok = False
            self.log(f"Embedded marker server failed: {type(e).__name__}: {e}")
            self.server_ready_event.set()
    #Start marker server thread if one not active. 
    def start_marker_server(self):
        if self.server_thread is not None and self.server_thread.is_alive():
            self.log("Embedded marker server thread is already running.")
            return self.server_ok
        #Reset state to prep for fresh start. 
        self.server_ok = False
        self.server_ready_event.clear()
        self.stop_server_event.clear()
        #Create the thread. daemon=True means it wont block process exit. 
        self.server_thread = threading.Thread(target=self.marker_server_worker,daemon=True)
        #Start that thread!
        self.server_thread.start()
        #Wait up to 5s for server to indicate 
        ready = self.server_ready_event.wait(timeout=5)
        #If no signal, throw error to log.
        if not ready:
            self.log("Embedded marker server did not signal readiness in time.")
            return False
        #If server not ok, cry.
        if not self.server_ok:
            self.log("Embedded marker server signaled failure.")
            return False
        #All good 
        self.log("Embedded marker server startup confirmed.")
        return True
    #Verifies the server is reachable over TCP 
    def wait_for_marker_server(self, timeout_seconds=5):
        #Start timeout timer
        start = time.time()
        while time.time() - start < timeout_seconds:
            if not self.server_ok: #If not ok, wait and try again. 
                time.sleep(0.25)
                continue

            try:
                with socket.create_connection((self.ANCHOR_HOST, self.ANCHOR_PORT), timeout=1): #Try making connection to server.
                    self.log("Embedded marker server is reachable.")
                    return True
            except OSError: #If connection fails, wait and try again until timeout.
                time.sleep(0.25)

        self.log("Embedded marker server was not reachable before timeout.")
        return False

    # Process handling

    #Returns currently running processes that match the NI I/O Trace executable.
    def get_instances(self):
        procs = []
        for p in psutil.process_iter(["name"]): #Request process name. 
            try:
                if (p.info["name"] or "").lower() == self.PROCESS_NAME.lower(): #If process matches "NI IO Trace.exe", store it. 
                    procs.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return procs

    #Launches NI I/O Trace if not already running. 
    def launch(self):
        existing_instances = self.get_instances()
        if existing_instances:
            self.log("Launch skipped: NI I/O Trace is already running.")
            return False

        exe_dir = os.path.dirname(self.EXE_PATH) #Gets folder containing executable. 
        subprocess.Popen(
            ["cmd", "/c", "start", "", "/D", exe_dir, self.EXE_PATH], #Launches through Windoes start cmd. 
            shell=False
        )
        self.log("Launched NI I/O Trace.")
        return True

    # UI automation

    #Wait for NI I/O Trace's UI window to exist and is connectable. 
    def wait_for_trace_window(self, timeout_seconds=15):
        start = time.time()

        while time.time() - start < timeout_seconds: #Loop until timeout.
            if self.get_instances(): #Only try UI automation if the process exists. 
                try:
                    #Connect to NI I/O Trace window using pywinauto.
                    app = Application(backend="uia").connect(title_re="NI I/O Trace.*", timeout=2) 
                    win = app.window(title_re="NI I/O Trace.*")
                    if win.exists():
                        return True
                except Exception:
                    pass #Continue until timeout. 
            time.sleep(0.5)

        self.log("Timed out waiting for NI I/O Trace window.")
        return False
    #Method to get the app and window objects. 
    def connect_trace_window(self):
        app = Application(backend="uia").connect(title_re="NI I/O Trace.*", timeout=5)
        win = app.window(title_re="NI I/O Trace.*")
        return app, win
    #Checks if NI I/O Trace indicates capture is on.
    def is_capture_on(self):
        try:
            #Checks for [capture on]. WARNING: UI-fragile, if NI changes their window format this will break.
            _, win = self.connect_trace_window()
            title = win.window_text() or ""
            return "[capture on]" in title.lower()
        except Exception as e:
            self.log(f"Could not determine capture state: {e}") #Crys if it cannot determine capture state. 
            return False
    #Attempts to start capture. 
    def start_capture(self):
        try:
            if not self.wait_for_trace_window(): #If not window, cry and skip.
                self.log("Cannot start capture because NI I/O Trace window was not found.")
                return False
            #Bring window to front so that UI automation will work.
            _, win = self.connect_trace_window()
            win.set_focus()
            time.sleep(0.5)
            #Double check that capture is not already on before trying to start it.
            if self.is_capture_on():
                self.log("NI I/O Trace capture is already ON.")
                return True
            #Initiate keyboard stroke F8 to turn on capture.
            try:
                win.type_keys("{F8}", set_foreground=True)
                time.sleep(1)
                if self.is_capture_on():
                    self.log("Started NI I/O Trace capture using F8.")
                    return True
            except Exception:
                pass
            #Throw a fit if capture still not on.
            self.log("Failed to start NI I/O Trace capture.")
            return False

        except Exception as e:
            self.log(f"Failed to start capture: {e}")
            return False

    # Anchor handling

    #Construct string ("anchor") written into the VISA socket.
    def build_anchor_message(self, event_type="heartbeat"):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S") #timestamp
        hostname = os.environ.get("COMPUTERNAME", "UNKNOWN_HOST") #Use windows machine name if available. 
        return f"TRACE_ANCHOR|{now}|HOST={hostname}|EVENT={event_type}" #Example result: "TRACE_ANCHOR|2024-06-30 14:23:45|HOST=MYPC|EVENT=heartbeat"
    #Sends one anchor through NI-VISA to embedded marker server. 
    def send_anchor(self, event_type="heartbeat"):
        if not self.server_ok: #Do not send if not ok
            self.log("Skipping anchor send because embedded marker server is not healthy.")
            return False
        #Builds anchor message. 
        message = self.build_anchor_message(event_type)
        #Predeclared variables for cleanup in finally block.
        rm = None
        inst = None

        try:
            #Create pyvisa resource manager with NI-VISA's DLL. 
            rm = pyvisa.ResourceManager(self.VISA_DLL)
            #Open socket
            inst = rm.open_resource(self.ANCHOR_RESOURCE)
            #Configure connection behavior. 
            inst.timeout = self.ANCHOR_OPEN_TIMEOUT_MS
            inst.write_termination = self.ANCHOR_WRITE_TERMINATION

            self.log(f"Sending anchor to {self.ANCHOR_RESOURCE}: {message}")
            inst.write(message) #Send anchor message. 
            #Records the time of the last anchor sent. 
            self.last_anchor_time = time.time()
            self.log(f"Anchor sent successfully: {message}")
            return True
        #Throw fit if anchor does not send.
        except Exception as e:
            self.log(f"Failed to send anchor: {type(e).__name__}: {e}")
            return False
        #Close instance after anchor sent. 
        finally:
            try:
                if inst is not None:
                    inst.close()
            except Exception:
                pass
    #Sends heartbeat anchor every five minutes. Do not send if server is not healthy.
    def maybe_send_periodic_anchor(self):
        if not self.server_ok:
            return

        now = time.time()
        if now - self.last_anchor_time >= self.ANCHOR_INTERVAL_SECONDS:
            self.send_anchor(event_type="heartbeat")
    #Setup routine used at startup and after restarts. 
    def initialize_trace_session(self, event_type):
        if not self.wait_for_trace_window():
            self.log("Skipping trace session initialization because window was not found.")
            return False

        capture_started = self.start_capture()
        if not capture_started:
            self.log("Capture was not confirmed ON; anchor may not be recorded.")

        if not self.server_ok:
            self.log("Embedded marker server is not healthy; anchor send skipped.")
            return False

        marker_ready = self.wait_for_marker_server(timeout_seconds=5)
        if not marker_ready:
            self.log("Embedded marker server is not ready; anchor send skipped.")
            return False
        #After checking server, trace window, and capture status, send anchor to mark startup or restart. 
        return self.send_anchor(event_type=event_type)

    # Main loop

    def run(self):
        self.log("NI I/O Trace keeper started.")
        #Start marker server. 
        server_started = self.start_marker_server()
        if not server_started:
            self.log("WARNING: Embedded marker server failed to start.")
        #Launch NI I/O Trace if not running and initialize trace session.
        instances = self.get_instances()
        if not instances:
            self.log("NI I/O Trace is not running at startup. Launching now.")
            self.launch()
            time.sleep(self.POST_LAUNCH_WAIT)
        #Record last state of whether NI I/O Trace is running. 
        last_seen_running = len(self.get_instances()) > 0
        #Start capture and send startup anchor
        if last_seen_running:
            self.log("NI I/O Trace confirmed running.")
            self.initialize_trace_session(event_type="startup")
        else:
            self.log("WARNING: NI I/O Trace could not be confirmed running after startup.")
        #Infinite watchdog loop.
        while True:
            #Refresh current process state.
            instances = self.get_instances()
            #Trace went bye bye. 
            if len(instances) == 0:
                #Only react if last seen state was running. 
                if last_seen_running:
                    self.log("NI I/O Trace exited. Waiting before restart...")
                    time.sleep(self.RESTART_DELAY)
                    #Launch and wait. 
                    instances = self.get_instances()
                    if len(instances) == 0:
                        launched = self.launch()
                        if launched:
                            self.log("Restarted NI I/O Trace.")
                            time.sleep(self.POST_LAUNCH_WAIT) #Launch and wait 

                            instances = self.get_instances() #Restore capture and send restart anchor. 
                            if len(instances) > 0:
                                self.initialize_trace_session(event_type="restart")

                last_seen_running = False #Update state to not running. 

            else:
                last_seen_running = True #It running
                #If capture turns off, turn back on and send anchor. 
                if not self.is_capture_on():
                    self.log("Capture appears OFF while NI I/O Trace is running. Attempting to start capture.")
                    self.start_capture()
                #Send heartbeat anchors. 
                self.maybe_send_periodic_anchor()

            time.sleep(self.CHECK_INTERVAL)

#Main entry point. Creates an instance of the NIIOTraceKeeper class and starts the watchdog loop.
def main():
    keeper = NIIOTraceKeeper()
    keeper.run()

if __name__ == "__main__":
    main()