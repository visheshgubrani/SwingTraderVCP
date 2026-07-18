import { useState, useEffect, useRef } from "react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useMutation,
} from "@tanstack/react-query";
import {
  ShieldCheck,
  Database,
  Play,
  Square,
  RefreshCw,
  Clock,
  Terminal,
  FileSpreadsheet,
  CheckCircle2,
  XCircle,
  Loader2,
  AlertTriangle,
  TrendingUp,
  Search,
  ListFilter,
} from "lucide-react";

const BACKEND_URL = "http://localhost:8000";
const queryClient = new QueryClient();

// A simple local router and context holder
function AppContent() {
  const [path, setPath] = useState(window.location.pathname);

  useEffect(() => {
    const handlePopState = () => {
      setPath(window.location.pathname);
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const navigate = (to: string) => {
    window.history.pushState({}, "", to);
    setPath(to);
  };

  if (path === "/callback") {
    return <Callback navigate={navigate} />;
  }

  return <Dashboard />;
}

// Callback component handles Fyers redirect
function Callback({ navigate }: { navigate: (to: string) => void }) {
  const [status, setStatus] = useState<"loading" | "success" | "error">("loading");
  const [errorMessage, setErrorMessage] = useState("");

  const exchangeToken = useMutation({
    mutationFn: async ({ code, state }: { code: string; state: string }) => {
      const response = await fetch(`${BACKEND_URL}/api/v1/auth/callback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, state }),
      });
      if (!response.ok) {
        const errData = await response.json();
        throw new Error(errData.detail || "Failed to exchange auth token.");
      }
      return response.json();
    },
    onSuccess: () => {
      setStatus("success");
      setTimeout(() => {
        navigate("/");
      }, 2000);
    },
    onError: (err: any) => {
      setStatus("error");
      setErrorMessage(err.message || "An error occurred.");
    },
  });

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const authCode = params.get("auth_code") || params.get("code");
    const state = params.get("state");
    const storedState = localStorage.getItem("fyers_auth_state");

    if (!authCode) {
      setStatus("error");
      setErrorMessage("No authorization code was found in the callback URL.");
      return;
    }

    if (state && storedState && state !== storedState) {
      // Log CSRF warning, but proceed since it's a single-user system
      console.warn("OAuth state mismatch. Expected: ", storedState, " got: ", state);
    }

    exchangeToken.mutate({ code: authCode, state: state || "" });
  }, []);

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-50 flex flex-col items-center justify-center p-6 font-sans">
      <div className="w-full max-w-md bg-zinc-900/60 border border-zinc-800 rounded-2xl p-8 backdrop-blur-md shadow-2xl text-center">
        {status === "loading" && (
          <div className="flex flex-col items-center py-6 space-y-4">
            <Loader2 className="h-12 w-12 text-emerald-500 animate-spin" />
            <h2 className="text-xl font-semibold">Exchanging Code...</h2>
            <p className="text-zinc-400 text-sm">
              Verifying your credentials and generating secure access tokens.
            </p>
          </div>
        )}

        {status === "success" && (
          <div className="flex flex-col items-center py-6 space-y-4 animate-in fade-in zoom-in-95 duration-300">
            <CheckCircle2 className="h-16 w-16 text-emerald-500" />
            <h2 className="text-2xl font-bold text-emerald-400">Authenticated!</h2>
            <p className="text-zinc-400 text-sm">
              Fyers token successfully encrypted and saved to the database.
            </p>
            <p className="text-xs text-zinc-500">Redirecting to Dashboard...</p>
          </div>
        )}

        {status === "error" && (
          <div className="flex flex-col items-center py-6 space-y-4 animate-in fade-in zoom-in-95 duration-300">
            <XCircle className="h-16 w-16 text-red-500" />
            <h2 className="text-2xl font-bold text-red-400">Authentication Failed</h2>
            <p className="text-zinc-400 text-sm">{errorMessage}</p>
            <button
              onClick={() => navigate("/")}
              className="mt-4 px-5 py-2 bg-zinc-800 hover:bg-zinc-700 active:bg-zinc-800 text-zinc-50 font-medium rounded-lg transition text-sm cursor-pointer"
            >
              Back to Dashboard
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// Dashboard component displays active states, metrics, and progress logs
function Dashboard() {
  const [syncYears, setSyncYears] = useState(1);
  const [validationYears, setValidationYears] = useState(2);
  const [terminalTab, setTerminalTab] = useState<"sync" | "validation">("sync");
  const [activeTab, setActiveTab] = useState<"admin" | "screener">("admin");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  // 1. Auth Status query
  const { data: authStatus } = useQuery({
    queryKey: ["authStatus"],
    queryFn: async () => {
      const response = await fetch(`${BACKEND_URL}/api/v1/auth/status`);
      if (!response.ok) throw new Error("Failed to load auth status");
      return response.json();
    },
    refetchInterval: 10000, // Check token every 10 seconds
  });

  // 2. Sync Status query
  const { data: syncStatus, refetch: refetchSync } = useQuery({
    queryKey: ["syncStatus"],
    queryFn: async () => {
      const response = await fetch(`${BACKEND_URL}/api/v1/historical/status`);
      if (!response.ok) throw new Error("Failed to load sync status");
      return response.json();
    },
    refetchInterval: (query) => {
      const data = query.state.data as any;
      return data?.is_running ? 1500 : 5000; // Poll faster when running
    },
  });

  // 2b. Validation Status query
  const { data: validationStatus, refetch: refetchValidation } = useQuery({
    queryKey: ["validationStatus"],
    queryFn: async () => {
      const response = await fetch(`${BACKEND_URL}/api/v1/historical/validate/status`);
      if (!response.ok) throw new Error("Failed to load validation status");
      return response.json();
    },
    refetchInterval: (query) => {
      const data = query.state.data as any;
      return data?.is_running ? 1500 : 5000; // Poll faster when running
    },
  });

  // 2c. Technical Scan Runs query
  const { data: scanRuns, refetch: refetchScanRuns } = useQuery({
    queryKey: ["scanRuns"],
    queryFn: async () => {
      const response = await fetch(`${BACKEND_URL}/api/v1/screening/runs`);
      if (!response.ok) throw new Error("Failed to load scan runs");
      return response.json();
    },
    refetchInterval: 5000, // Poll every 5 seconds for status updates
  });

  // 2d. Technical Scan Results query
  const { data: scanResults, isPending: isLoadingResults } = useQuery({
    queryKey: ["scanResults", selectedRunId],
    queryFn: async () => {
      if (!selectedRunId) return [];
      const response = await fetch(`${BACKEND_URL}/api/v1/screening/runs/${selectedRunId}/results`);
      if (!response.ok) throw new Error("Failed to load scan results");
      return response.json();
    },
    enabled: !!selectedRunId,
  });

  // 3. Initiate Fyers login redirect
  const authenticateFyers = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/v1/auth/url`);
      if (!response.ok) throw new Error("Failed to fetch Fyers Login URL");
      const data = await response.json();
      localStorage.setItem("fyers_auth_state", data.state);
      window.location.href = data.url;
    } catch (err: any) {
      alert("Error: " + err.message);
    }
  };

  // 4. Trigger historical sync mutation
  const triggerSync = useMutation({
    mutationFn: async (years: number) => {
      const response = await fetch(`${BACKEND_URL}/api/v1/historical/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ years }),
      });
      if (!response.ok) {
        const errData = await response.json();
        throw new Error(errData.detail || "Failed to trigger historical sync.");
      }
      return response.json();
    },
    onSuccess: () => {
      refetchSync();
      setTerminalTab("sync");
    },
    onError: (err: any) => {
      alert("Error triggering sync: " + err.message);
    },
  });

  // 4b. Trigger data validation mutation
  const triggerValidation = useMutation({
    mutationFn: async (years: number) => {
      const response = await fetch(`${BACKEND_URL}/api/v1/historical/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ years }),
      });
      if (!response.ok) {
        const errData = await response.json();
        throw new Error(errData.detail || "Failed to trigger validation.");
      }
      return response.json();
    },
    onSuccess: () => {
      refetchValidation();
      setTerminalTab("validation");
    },
    onError: (err: any) => {
      alert("Error triggering validation: " + err.message);
    },
  });

  // 5. Cancel historical sync mutation
  const cancelSync = useMutation({
    mutationFn: async () => {
      const response = await fetch(`${BACKEND_URL}/api/v1/historical/cancel`, {
        method: "POST",
      });
      if (!response.ok) throw new Error("Failed to cancel sync.");
      return response.json();
    },
    onSuccess: () => {
      refetchSync();
    },
  });

  // 5b. Cancel data validation mutation
  const cancelValidation = useMutation({
    mutationFn: async () => {
      const response = await fetch(`${BACKEND_URL}/api/v1/historical/validate/cancel`, {
        method: "POST",
      });
      if (!response.ok) throw new Error("Failed to cancel validation.");
      return response.json();
    },
    onSuccess: () => {
      refetchValidation();
    },
  });

  // Auto-scroll logs terminal
  useEffect(() => {
    if (logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [syncStatus?.logs, validationStatus?.logs, terminalTab]);

  // Auto tab-switching on run status change
  useEffect(() => {
    if (syncStatus?.is_running) {
      setTerminalTab("sync");
    }
  }, [syncStatus?.is_running]);

  useEffect(() => {
    if (validationStatus?.is_running) {
      setTerminalTab("validation");
    }
  }, [validationStatus?.is_running]);

  // 4c. Trigger Technical Scan mutation
  const triggerScan = useMutation({
    mutationFn: async () => {
      const response = await fetch(`${BACKEND_URL}/api/v1/screening/scan`, {
        method: "POST",
      });
      if (!response.ok) throw new Error("Failed to trigger technical scan.");
      return response.json();
    },
    onSuccess: (data) => {
      refetchScanRuns();
      setSelectedRunId(data.scan_run_id);
      setActiveTab("screener");
    },
    onError: (err: any) => {
      alert("Error triggering scan: " + err.message);
    },
  });

  // Auto-select latest successful run
  useEffect(() => {
    if (scanRuns && scanRuns.length > 0 && !selectedRunId) {
      const firstSuccess = scanRuns.find((r: any) => r.status === "succeeded");
      if (firstSuccess) {
        setSelectedRunId(firstSuccess.id);
      } else {
        setSelectedRunId(scanRuns[0].id);
      }
    }
  }, [scanRuns, selectedRunId]);

  const [symbolFilter, setSymbolFilter] = useState("");

  const isRunning = syncStatus?.is_running;
  const progressPercent =
    syncStatus?.total_symbols > 0
      ? Math.round((syncStatus.current_index / syncStatus.total_symbols) * 100)
      : 0;

  const isScanRunning = scanRuns?.some((r: any) => r.status === "running" || r.status === "queued");
  
  // Filter scan results in the frontend by symbol search
  const filteredResults = scanResults
    ? scanResults.filter((r: any) => r.symbol.toLowerCase().includes(symbolFilter.toLowerCase()))
    : [];

  const activeRun = scanRuns?.find((r: any) => r.id === selectedRunId);

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 font-sans antialiased p-6 md:p-12 selection:bg-emerald-500/30">
      <div className="max-w-6xl mx-auto space-y-8">
        
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-zinc-800 pb-6">
          <div>
            <h1 className="text-3xl font-extrabold tracking-tight bg-gradient-to-r from-zinc-50 via-zinc-200 to-zinc-400 bg-clip-text text-transparent">
              SwingTrader VCP Backend
            </h1>
            <p className="text-zinc-400 text-sm mt-1">
              Historical candle fetching system, data health reports & technical screening.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-zinc-500 bg-zinc-900 border border-zinc-800 px-3 py-1.5 rounded-full font-mono">
              v1.1.0
            </span>
          </div>
        </div>

        {/* Top Navigation Tabs */}
        <div className="flex gap-2 bg-zinc-900/30 border border-zinc-800/80 p-1.5 rounded-2xl max-w-md">
          <button
            onClick={() => setActiveTab("admin")}
            className={`flex-1 px-4 py-2.5 rounded-xl text-xs font-bold transition cursor-pointer text-center ${
              activeTab === "admin"
                ? "bg-zinc-800 border border-zinc-700 text-zinc-100 shadow"
                : "border-transparent text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Data Ingestion & Admin
          </button>
          <button
            onClick={() => setActiveTab("screener")}
            className={`flex-1 px-4 py-2.5 rounded-xl text-xs font-bold transition cursor-pointer text-center ${
              activeTab === "screener"
                ? "bg-zinc-800 border border-zinc-700 text-zinc-100 shadow"
                : "border-transparent text-zinc-400 hover:text-zinc-200"
            }`}
          >
            Technical Screener
          </button>
        </div>

        {/* Tab content 1: Admin & Ingestion */}
        {activeTab === "admin" && (
          <>
            {/* Dashboard Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
              
              {/* Card 1: Auth Status */}
              <div className="bg-zinc-900/50 border border-zinc-800 rounded-2xl p-6 backdrop-blur flex flex-col justify-between shadow-lg hover:border-zinc-700/60 transition">
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <h3 className="text-lg font-bold text-zinc-200 flex items-center gap-2">
                      <ShieldCheck className="h-5 w-5 text-emerald-400" />
                      Broker Authentication
                    </h3>
                    {authStatus?.authenticated ? (
                      <span className="text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 rounded-full font-semibold animate-pulse">
                        Connected
                      </span>
                    ) : (
                      <span className="text-xs text-red-400 bg-red-500/10 border border-red-500/30 px-2 py-0.5 rounded-full font-semibold">
                        Disconnected
                      </span>
                    )}
                  </div>

                  <p className="text-zinc-400 text-sm leading-relaxed">
                    {authStatus?.authenticated
                      ? "Connected to the Fyers API. Your access token is valid and encrypted securely in the DB."
                      : "Fyers API requires daily manual authentication. Please log in to retrieve your access token."}
                  </p>

                  {authStatus?.authenticated && (
                    <div className="bg-zinc-950/40 rounded-xl p-3 border border-zinc-800/60 flex items-center gap-2 text-xs font-mono text-zinc-400">
                      <Clock className="h-4 w-4 text-zinc-500 shrink-0" />
                      <div>
                        <div className="text-zinc-500 text-[10px] uppercase font-sans font-bold">Token Expires At</div>
                        <span className="text-zinc-300">
                          {new Date(authStatus.expires_at).toLocaleString()}
                        </span>
                      </div>
                    </div>
                  )}
                </div>

                <button
                  onClick={authenticateFyers}
                  className={`w-full mt-6 py-2.5 rounded-xl font-semibold transition text-sm cursor-pointer ${
                    authStatus?.authenticated
                      ? "bg-zinc-800 hover:bg-zinc-700 active:bg-zinc-800 text-zinc-200"
                      : "bg-emerald-500 hover:bg-emerald-400 active:bg-emerald-600 text-zinc-950"
                  }`}
                >
                  {authStatus?.authenticated ? "Re-authenticate Fyers" : "Login with Fyers"}
                </button>
              </div>

              {/* Card 2: Database Stats */}
              <div className="bg-zinc-900/50 border border-zinc-800 rounded-2xl p-6 backdrop-blur flex flex-col justify-between shadow-lg hover:border-zinc-700/60 transition">
                <div className="space-y-4">
                  <h3 className="text-lg font-bold text-zinc-200 flex items-center gap-2">
                    <Database className="h-5 w-5 text-indigo-400" />
                    Database Stats
                  </h3>
                  <p className="text-zinc-400 text-sm leading-relaxed">
                    Current candle records stored in local PostgreSQL tables.
                  </p>

                  <div className="grid grid-cols-2 gap-4 mt-2">
                    <div className="bg-zinc-950/40 border border-zinc-800/60 p-4 rounded-xl text-center">
                      <div className="text-2xl font-bold font-mono text-indigo-400">
                        {syncStatus?.db_metrics?.nifty500_instruments ?? 0}
                      </div>
                      <div className="text-zinc-500 text-[10px] uppercase font-medium mt-1">
                        Nifty 500 Stocks
                      </div>
                    </div>
                    <div className="bg-zinc-950/40 border border-zinc-800/60 p-4 rounded-xl text-center">
                      <div className="text-2xl font-bold font-mono text-indigo-400">
                        {(syncStatus?.db_metrics?.total_candles ?? 0).toLocaleString()}
                      </div>
                      <div className="text-zinc-500 text-[10px] uppercase font-medium mt-1">
                        Total Daily Candles
                      </div>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2 text-xs text-zinc-500 mt-6 bg-zinc-950/20 p-2.5 rounded-lg border border-zinc-900">
                  <FileSpreadsheet className="h-4 w-4 text-zinc-600" />
                  <span>Universe: Nifty 500 Daily resolution</span>
                </div>
              </div>

              {/* Card 3: Historical Sync Controls */}
              <div className="bg-zinc-900/50 border border-zinc-800 rounded-2xl p-6 backdrop-blur flex flex-col justify-between shadow-lg hover:border-zinc-700/60 transition">
                <div className="space-y-4">
                  <h3 className="text-lg font-bold text-zinc-200 flex items-center gap-2">
                    <RefreshCw className={`h-5 w-5 text-blue-400 ${isRunning ? "animate-spin" : ""}`} />
                    Historical Sync
                  </h3>
                  <p className="text-zinc-400 text-sm leading-relaxed">
                    Sync historical daily candles from Fyers. Automatically respects API rate limits (10 req/s, 200 req/m).
                  </p>

                  {isRunning ? (
                    <div className="space-y-2 mt-2">
                      <div className="flex justify-between text-xs text-zinc-400">
                        <span>Syncing: <strong className="text-zinc-200">{syncStatus.current_symbol}</strong></span>
                        <span>{syncStatus.current_index} / {syncStatus.total_symbols}</span>
                      </div>
                      <div className="w-full bg-zinc-800 rounded-full h-2.5 overflow-hidden">
                        <div
                          className="bg-blue-500 h-2.5 rounded-full transition-all duration-500"
                          style={{ width: `${progressPercent}%` }}
                        ></div>
                      </div>
                      <div className="flex justify-between text-[10px] text-zinc-500 font-mono">
                        <span>{progressPercent}% Complete</span>
                        <span>~{Math.round((syncStatus.total_symbols - syncStatus.current_index) * 0.35)}s left</span>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-center gap-3 mt-2">
                      <span className="text-sm text-zinc-400 font-medium">Data range:</span>
                      <select
                        value={syncYears}
                        onChange={(e) => setSyncYears(Number(e.target.value))}
                        className="bg-zinc-950 border border-zinc-800 text-zinc-200 rounded-lg px-3 py-1.5 text-xs font-medium focus:border-zinc-700 outline-none cursor-pointer"
                      >
                        <option value={1}>1 Year (Recommended)</option>
                        <option value={2}>2 Years</option>
                      </select>
                    </div>
                  )}
                </div>

                {isRunning ? (
                  <button
                    onClick={() => cancelSync.mutate()}
                    disabled={cancelSync.isPending}
                    className="w-full mt-6 py-2.5 bg-red-950 hover:bg-red-900 border border-red-800 text-red-200 font-semibold rounded-xl transition text-sm cursor-pointer flex items-center justify-center gap-2"
                  >
                    <Square className="h-4 w-4" /> Stop Sync Run
                  </button>
                ) : (
                  <button
                    onClick={() => triggerSync.mutate(syncYears)}
                    disabled={!authStatus?.authenticated || triggerSync.isPending}
                    className={`w-full mt-6 py-2.5 font-semibold rounded-xl transition text-sm flex items-center justify-center gap-2 cursor-pointer ${
                      authStatus?.authenticated
                        ? "bg-blue-500 hover:bg-blue-400 active:bg-blue-600 text-zinc-950"
                        : "bg-zinc-800 text-zinc-500 cursor-not-allowed border border-zinc-800"
                    }`}
                  >
                    <Play className="h-4 w-4" /> Trigger Historical Sync
                  </button>
                )}
              </div>

              {/* Card 4: Data Validation Controls */}
              <div className="bg-zinc-900/50 border border-zinc-800 rounded-2xl p-6 backdrop-blur flex flex-col justify-between shadow-lg hover:border-zinc-700/60 transition">
                <div className="space-y-4">
                  <h3 className="text-lg font-bold text-zinc-200 flex items-center gap-2">
                    <ShieldCheck className={`h-5 w-5 text-emerald-400 ${validationStatus?.is_running ? "animate-pulse" : ""}`} />
                    Data Validation
                  </h3>
                  <p className="text-zinc-400 text-sm leading-relaxed">
                    Validate completeness, symbol coverage, and scan for corporate action price anomalies (splits/bonuses).
                  </p>

                  {validationStatus?.is_running ? (
                    <div className="space-y-2 mt-2">
                      <div className="flex justify-between text-xs text-zinc-400">
                        <span>Validating: <strong className="text-zinc-200">{validationStatus.current_symbol}</strong></span>
                        <span>{validationStatus.current_index} / {validationStatus.total_symbols}</span>
                      </div>
                      <div className="w-full bg-zinc-800 rounded-full h-2.5 overflow-hidden">
                        <div
                          className="bg-emerald-500 h-2.5 rounded-full transition-all duration-500"
                          style={{ width: `${validationStatus.total_symbols > 0 ? Math.round((validationStatus.current_index / validationStatus.total_symbols) * 100) : 0}%` }}
                        ></div>
                      </div>
                      <div className="flex justify-between text-[10px] text-zinc-500 font-mono">
                        <span>{validationStatus.total_symbols > 0 ? Math.round((validationStatus.current_index / validationStatus.total_symbols) * 100) : 0}% Complete</span>
                        <span>~{Math.round((validationStatus.total_symbols - validationStatus.current_index) * 0.15)}s left</span>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-center gap-3 mt-2">
                      <span className="text-sm text-zinc-400 font-medium">Range:</span>
                      <select
                        value={validationYears}
                        onChange={(e) => setValidationYears(Number(e.target.value))}
                        className="bg-zinc-950 border border-zinc-800 text-zinc-200 rounded-lg px-3 py-1.5 text-xs font-medium focus:border-zinc-700 outline-none cursor-pointer"
                      >
                        <option value={1}>1 Year Check</option>
                        <option value={2}>2 Years Check</option>
                      </select>
                    </div>
                  )}
                </div>

                {validationStatus?.is_running ? (
                  <button
                    onClick={() => cancelValidation.mutate()}
                    disabled={cancelValidation.isPending}
                    className="w-full mt-6 py-2.5 bg-red-950 hover:bg-red-900 border border-red-800 text-red-200 font-semibold rounded-xl transition text-sm cursor-pointer flex items-center justify-center gap-2"
                  >
                    <Square className="h-4 w-4" /> Stop Validation
                  </button>
                ) : (
                  <button
                    onClick={() => triggerValidation.mutate(validationYears)}
                    disabled={triggerValidation.isPending}
                    className={`w-full mt-6 py-2.5 font-semibold rounded-xl transition text-sm flex items-center justify-center gap-2 cursor-pointer ${
                      triggerValidation.isPending
                        ? "bg-zinc-800 text-zinc-500 cursor-not-allowed border border-zinc-800"
                        : "bg-emerald-500 hover:bg-emerald-400 active:bg-emerald-600 text-zinc-950"
                    }`}
                  >
                    <Play className="h-4 w-4" /> Run Validation
                  </button>
                )}
              </div>

            </div>

            {/* Validation Report & Terminals Layout */}
            <div className={`grid grid-cols-1 ${validationStatus?.report?.validation_timestamp ? "lg:grid-cols-5" : "lg:grid-cols-1"} gap-6`}>
              
              {/* Detailed Data Health Report (Left side, takes 3/5 width on lg screens) */}
              {validationStatus?.report?.validation_timestamp && (
                <div className="lg:col-span-3 bg-zinc-900/40 border border-zinc-800 rounded-2xl p-6 shadow-xl space-y-6 flex flex-col justify-between">
                  <div>
                    <div className="flex items-center justify-between border-b border-zinc-800/80 pb-3 mb-4">
                      <div>
                        <h3 className="text-lg font-bold text-zinc-200 flex items-center gap-2">
                          <ShieldCheck className="h-5 w-5 text-emerald-400" />
                          Data Health Report
                        </h3>
                        <p className="text-xs text-zinc-500 mt-0.5">
                          Checked {validationStatus.report.years_checked} {validationStatus.report.years_checked === 1 ? "year" : "years"} history ({validationStatus.report.total_instruments_checked} instruments, {validationStatus.report.ref_trading_days_count} trading days)
                        </p>
                      </div>
                      <span className="text-[10px] text-zinc-500 font-mono bg-zinc-950 px-2 py-1 rounded border border-zinc-900">
                        {new Date(validationStatus.report.validation_timestamp).toLocaleTimeString()}
                      </span>
                    </div>

                    {/* Summary Metrics */}
                    <div className="grid grid-cols-3 gap-4 mb-5">
                      <div className={`p-4 rounded-xl border text-center ${validationStatus.report.coverage_check.passed ? "bg-emerald-500/5 border-emerald-500/20" : "bg-red-500/5 border-red-500/20"}`}>
                        <div className="text-xs font-semibold text-zinc-400">Coverage</div>
                        <div className={`text-sm font-bold mt-1 ${validationStatus.report.coverage_check.passed ? "text-emerald-400" : "text-red-400"}`}>
                          {validationStatus.report.coverage_check.passed ? "100% OK" : `${validationStatus.report.coverage_check.missing_count} Missing`}
                        </div>
                      </div>

                      <div className={`p-4 rounded-xl border text-center ${validationStatus.report.completeness_check.passed ? "bg-emerald-500/5 border-emerald-500/20" : "bg-amber-500/5 border-amber-500/20"}`}>
                        <div className="text-xs font-semibold text-zinc-400">Completeness</div>
                        <div className={`text-sm font-bold mt-1 ${validationStatus.report.completeness_check.passed ? "text-emerald-400" : "text-amber-400"}`}>
                          {validationStatus.report.completeness_check.passed ? "100% Complete" : `${validationStatus.report.completeness_check.gaps_count} Gaps`}
                        </div>
                      </div>

                      {(() => {
                        const spotFailed = validationStatus.report.corporate_actions.spot_checks.some((sc: any) => sc.status !== "PASSED");
                        const anomalies = validationStatus.report.corporate_actions.anomalies_found_count;
                        const passed = !spotFailed && anomalies === 0;
                        return (
                          <div className={`p-4 rounded-xl border text-center ${passed ? "bg-emerald-500/5 border-emerald-500/20" : "bg-red-500/5 border-red-500/20"}`}>
                            <div className="text-xs font-semibold text-zinc-400">Corp Actions</div>
                            <div className={`text-sm font-bold mt-1 ${passed ? "text-emerald-400" : "text-red-400"}`}>
                              {passed ? "No Cliffs" : `${anomalies} Cliffs`}
                            </div>
                          </div>
                        );
                      })()}
                    </div>

                    {/* Detailed Findings Scroll Area */}
                    <div className="space-y-4 max-h-[300px] overflow-y-auto pr-2 custom-scrollbar">
                      
                      {/* 1. Missing Symbols */}
                      {!validationStatus.report.coverage_check.passed && (
                        <div className="bg-red-500/5 border border-red-500/20 rounded-xl p-4 space-y-2">
                          <h4 className="text-xs font-bold text-red-400 uppercase tracking-wider flex items-center gap-1.5">
                            <AlertTriangle className="h-3.5 w-3.5" /> Missing Universe Symbols
                          </h4>
                          <div className="grid grid-cols-2 gap-2 text-xs font-mono text-zinc-300">
                            {validationStatus.report.coverage_check.missing_symbols.map((s: any) => (
                              <div key={s.symbol} className="bg-zinc-950/40 p-1.5 rounded border border-zinc-900 flex justify-between">
                                <span className="text-zinc-200 font-bold">{s.symbol}</span>
                                <span className="text-zinc-500 text-[10px] max-w-[120px] truncate">{s.name}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* 2. Completeness Gaps */}
                      {!validationStatus.report.completeness_check.passed && (
                        <div className="bg-amber-500/5 border border-amber-500/20 rounded-xl p-4 space-y-2">
                          <h4 className="text-xs font-bold text-amber-400 uppercase tracking-wider flex items-center gap-1.5">
                            <AlertTriangle className="h-3.5 w-3.5" /> Gaps & Missing Candles
                          </h4>
                          <div className="space-y-2">
                            {validationStatus.report.completeness_check.symbols_with_gaps.map((g: any) => (
                              <div key={g.symbol} className="bg-zinc-950/40 p-3 rounded-lg border border-zinc-900/60 text-xs flex flex-col space-y-1">
                                <div className="flex justify-between items-center">
                                  <span className="font-bold text-zinc-200">{g.symbol}</span>
                                  <span className="text-[10px] bg-amber-500/10 text-amber-400 border border-amber-500/20 px-2 py-0.5 rounded-full font-semibold">
                                    {g.gap_count} gaps
                                  </span>
                                </div>
                                <div className="text-[10px] text-zinc-400">
                                  First candle: <span className="text-zinc-300 font-mono">{g.first_candle}</span>
                                  {g.is_recent_listing && <span className="ml-2 text-emerald-400">(IPO listed)</span>}
                                </div>
                                <div className="text-[10px] text-zinc-500 font-mono leading-relaxed truncate">
                                  Dates: {g.gaps.join(", ")}{g.has_more_gaps && " ..."}
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* 3. Corporate Action Spot-Checks */}
                      <div className="bg-zinc-950/40 border border-zinc-800 rounded-xl p-4 space-y-4">
                        <div>
                          <h4 className="text-xs font-bold text-zinc-300 uppercase tracking-wider mb-2">Corporate Action Spot-Checks</h4>
                          <div className="space-y-2">
                            {validationStatus.report.corporate_actions.spot_checks.map((sc: any) => (
                              <div key={sc.symbol} className="flex justify-between items-center bg-zinc-900/50 p-2.5 rounded-lg border border-zinc-850 text-xs">
                                <div>
                                  <span className="font-bold text-zinc-200">{sc.symbol}</span>
                                  <span className="text-[10px] text-zinc-500 ml-2 font-mono">Ex-Date: {sc.ex_date}</span>
                                </div>
                                <div className="flex items-center gap-2">
                                  <span className="text-[10px] text-zinc-400 font-mono italic max-w-[200px] truncate">{sc.details}</span>
                                  <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold ${sc.status === "PASSED" ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/25" : "bg-red-500/10 text-red-400 border border-red-500/25"}`}>
                                    {sc.status}
                                  </span>
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>

                        {/* 4. Anomalies Scan */}
                        <div>
                          <h4 className="text-xs font-bold text-zinc-300 uppercase tracking-wider mb-2">
                            Price Cliff Anomalies (Drops &gt; 35%)
                          </h4>
                          {validationStatus.report.corporate_actions.anomalies_found_count > 0 ? (
                            <div className="space-y-2">
                              {validationStatus.report.corporate_actions.anomalies.map((anom: any, idx: number) => (
                                <div key={idx} className="flex justify-between items-center bg-red-500/5 border border-red-500/15 p-2.5 rounded-lg text-xs">
                                  <div>
                                    <span className="font-bold text-red-400">{anom.symbol}</span>
                                    <span className="text-[10px] text-zinc-500 ml-2 font-mono">{anom.date}</span>
                                  </div>
                                  <div className="text-right">
                                    <div className="font-bold text-zinc-200">-{anom.pct_drop}%</div>
                                    <div className="text-[9px] text-zinc-500 font-mono">
                                      {anom.prev_close.toFixed(2)} &rarr; {anom.close.toFixed(2)}
                                    </div>
                                  </div>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <div className="text-xs text-emerald-400 italic bg-emerald-500/5 p-2.5 rounded-lg border border-emerald-500/15">
                              No single-day price drops &gt; 35% detected in the database.
                            </div>
                          )}
                        </div>
                      </div>

                    </div>
                  </div>
                  
                  <div className="mt-4 text-[10px] text-zinc-500 flex justify-between border-t border-zinc-800/40 pt-3">
                    <span>Validation Job v1.0</span>
                    <span>Run a new check to refresh health status</span>
                  </div>
                </div>
              )}

              {/* Terminal Box (Right side, takes 2/5 width if report is visible, else takes full width) */}
              <div className={`bg-zinc-900/40 border border-zinc-800 rounded-2xl p-6 shadow-xl space-y-4 flex flex-col justify-between ${validationStatus?.report?.validation_timestamp ? "lg:col-span-2" : "w-full"}`}>
                <div>
                  <div className="flex items-center justify-between border-b border-zinc-800/80 pb-3 mb-3">
                    {/* Tabs to switch logs */}
                    <div className="flex gap-2">
                      <button
                        onClick={() => setTerminalTab("sync")}
                        className={`text-xs font-bold flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition border cursor-pointer ${
                          terminalTab === "sync"
                            ? "bg-zinc-800 border-zinc-700 text-zinc-100"
                            : "border-transparent text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900"
                        }`}
                      >
                        <Terminal className="h-3.5 w-3.5" />
                        Sync Logs
                      </button>
                      <button
                        onClick={() => setTerminalTab("validation")}
                        className={`text-xs font-bold flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition border cursor-pointer ${
                          terminalTab === "validation"
                            ? "bg-zinc-800 border-zinc-700 text-zinc-100"
                            : "border-transparent text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900"
                        }`}
                      >
                        <Terminal className="h-3.5 w-3.5" />
                        Validation Logs
                      </button>
                    </div>

                    {/* Errors Tag */}
                    <div>
                      {terminalTab === "sync" ? (
                        syncStatus?.errors?.length > 0 && (
                          <span className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 px-2 py-0.5 rounded-full font-mono flex items-center gap-1.5">
                            <AlertTriangle className="h-3 w-3" />
                            {syncStatus.errors.length} Errors
                          </span>
                        )
                      ) : (
                        validationStatus?.errors?.length > 0 && (
                          <span className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 px-2 py-0.5 rounded-full font-mono flex items-center gap-1.5">
                            <AlertTriangle className="h-3 w-3" />
                            {validationStatus.errors.length} Errors
                          </span>
                        )
                      )}
                    </div>
                  </div>

                  {/* Logs Terminal */}
                  <div className="bg-zinc-950 border border-zinc-900 p-4 rounded-xl font-mono text-[11px] text-zinc-300 h-80 overflow-y-auto space-y-1.5 shadow-inner custom-scrollbar">
                    {terminalTab === "sync" ? (
                      syncStatus?.logs && syncStatus.logs.length > 0 ? (
                        syncStatus.logs.map((log: string, idx: number) => {
                          let colorClass = "text-zinc-400";
                          if (log.includes("ERROR")) colorClass = "text-red-400 font-semibold";
                          else if (log.includes("completed") || log.includes("Success")) colorClass = "text-emerald-400";
                          else if (log.includes("Syncing") || log.includes("Fetching")) colorClass = "text-blue-400";

                          return (
                            <div key={idx} className={colorClass}>
                              {log}
                            </div>
                          );
                        })
                      ) : (
                        <div className="text-zinc-600 italic flex h-full items-center justify-center">
                          Sync terminal idle. Trigger a sync process to view live logs.
                        </div>
                      )
                    ) : (
                      validationStatus?.logs && validationStatus.logs.length > 0 ? (
                        validationStatus.logs.map((log: string, idx: number) => {
                          let colorClass = "text-zinc-400";
                          if (log.includes("ERROR")) colorClass = "text-red-400 font-semibold";
                          else if (log.includes("completed") || log.includes("successful") || log.includes("completed successfully")) colorClass = "text-emerald-400";
                          else if (log.includes("ANOMALY")) colorClass = "text-red-300 font-medium";
                          else if (log.includes("WARNING")) colorClass = "text-amber-400";
                          else if (log.includes("Validating") || log.includes("Loading")) colorClass = "text-blue-400";

                          return (
                            <div key={idx} className={colorClass}>
                              {log}
                            </div>
                          );
                        })
                      ) : (
                        <div className="text-zinc-600 italic flex h-full items-center justify-center">
                          Validation terminal idle. Trigger a validation run to view live logs.
                        </div>
                      )
                    )}
                    <div ref={logEndRef} />
                  </div>
                </div>
                
                <div className="mt-4 text-[10px] text-zinc-500 font-mono border-t border-zinc-800/40 pt-3">
                  Terminal: {terminalTab === "sync" ? "Sync Output" : "Validation Output"}
                </div>
              </div>

            </div>
          </>
        )}

        {/* Tab content 2: Technical Screener */}
        {activeTab === "screener" && (
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 animate-in fade-in duration-300">
            
            {/* Sidebar: Scan Runs History (1/4 width) */}
            <div className="lg:col-span-1 bg-zinc-900/50 border border-zinc-800 rounded-2xl p-5 flex flex-col justify-between h-[650px] shadow-lg">
              <div className="space-y-4 overflow-y-auto pr-1 custom-scrollbar">
                <div className="flex justify-between items-center mb-1">
                  <h3 className="font-bold text-sm text-zinc-200 uppercase tracking-wider flex items-center gap-1.5">
                    <ListFilter className="h-4 w-4 text-indigo-400" />
                    Scan History
                  </h3>
                  {isScanRunning && (
                    <span className="flex h-2 w-2 relative">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-indigo-500"></span>
                    </span>
                  )}
                </div>
                
                <button
                  onClick={() => triggerScan.mutate()}
                  disabled={isScanRunning || triggerScan.isPending}
                  className={`w-full py-2.5 rounded-xl font-bold text-xs flex items-center justify-center gap-2 cursor-pointer transition ${
                    isScanRunning || triggerScan.isPending
                      ? "bg-zinc-800 text-zinc-500 cursor-not-allowed border border-zinc-800"
                      : "bg-indigo-500 hover:bg-indigo-400 text-zinc-50"
                  }`}
                >
                  {isScanRunning ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> Scanning...
                    </>
                  ) : (
                    <>
                      <Play className="h-3.5 w-3.5" /> Run Technical Scan
                    </>
                  )}
                </button>
                
                {/* List of past runs */}
                <div className="space-y-2">
                  {scanRuns && scanRuns.length > 0 ? (
                    scanRuns.map((run: any) => {
                      const isSelected = run.id === selectedRunId;
                      let statusColor = "text-zinc-500";
                      let statusBg = "bg-zinc-950";
                      
                      if (run.status === "succeeded") {
                        statusColor = "text-emerald-400";
                        statusBg = "bg-emerald-500/5";
                      } else if (run.status === "failed") {
                        statusColor = "text-red-400";
                        statusBg = "bg-red-500/5";
                      } else if (run.status === "running" || run.status === "queued") {
                        statusColor = "text-indigo-400";
                        statusBg = "bg-indigo-500/5";
                      }

                      return (
                        <div
                          key={run.id}
                          onClick={() => setSelectedRunId(run.id)}
                          className={`p-3 rounded-xl border text-left cursor-pointer transition ${
                            isSelected
                              ? "bg-zinc-800/80 border-indigo-500/50 shadow-md"
                              : "bg-zinc-950/40 border-zinc-800/80 hover:border-zinc-700/60"
                          }`}
                        >
                          <div className="flex justify-between items-center mb-1">
                            <span className={`text-[9px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded ${statusBg} ${statusColor}`}>
                              {run.status}
                            </span>
                            <span className="text-[10px] text-zinc-500 font-mono">
                              {new Date(run.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                            </span>
                          </div>
                          
                          <div className="text-xs font-semibold text-zinc-300 mt-1.5 flex justify-between">
                            <span>{new Date(run.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}</span>
                            {run.status === "succeeded" && (
                              <strong className="text-indigo-400 font-bold">{run.passing_count} hits</strong>
                            )}
                          </div>
                        </div>
                      );
                    })
                  ) : (
                    <div className="text-xs text-zinc-600 italic py-4 text-center">
                      No scan execution logs.
                    </div>
                  )}
                </div>
              </div>
              
              <div className="text-[9px] text-zinc-600 border-t border-zinc-800/40 pt-2 font-mono">
                Redis arq scanner active
              </div>
            </div>

            {/* Results Section (3/4 width) */}
            <div className="lg:col-span-3 bg-zinc-900/50 border border-zinc-800 rounded-2xl p-6 flex flex-col justify-between h-[650px] shadow-lg">
              <div className="space-y-4 flex flex-col h-full overflow-hidden">
                
                {/* Results Header */}
                <div className="flex flex-col md:flex-row md:items-center justify-between border-b border-zinc-800/80 pb-4 gap-3 shrink-0">
                  <div>
                    <h3 className="text-lg font-bold text-zinc-200 flex items-center gap-2">
                      <TrendingUp className="h-5 w-5 text-indigo-400" />
                      Scan Results: Nifty 500 Universe
                    </h3>
                    <p className="text-xs text-zinc-500 mt-0.5 font-mono">
                      {activeRun 
                        ? `Run ID: ${activeRun.id.substring(0, 8)}... | Executed at: ${new Date(activeRun.created_at).toLocaleString()}`
                        : "Select a scan run to view details"
                      }
                    </p>
                  </div>
                  
                  {/* Symbol Search Bar */}
                  {activeRun?.status === "succeeded" && (
                    <div className="relative w-full md:w-48">
                      <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-zinc-500" />
                      <input
                        type="text"
                        placeholder="Search symbol..."
                        value={symbolFilter}
                        onChange={(e) => setSymbolFilter(e.target.value)}
                        className="bg-zinc-950 border border-zinc-800 text-zinc-200 rounded-xl pl-8 pr-3 py-1.5 text-xs focus:border-zinc-700 outline-none w-full placeholder:text-zinc-600"
                      />
                    </div>
                  )}
                </div>

                {/* Results Table Area */}
                <div className="flex-1 overflow-y-auto custom-scrollbar pr-1">
                  {isLoadingResults ? (
                    <div className="flex flex-col items-center justify-center h-full py-12 space-y-3">
                      <Loader2 className="h-8 w-8 text-indigo-400 animate-spin" />
                      <span className="text-xs text-zinc-500 font-medium">Loading filtered symbols...</span>
                    </div>
                  ) : activeRun?.status === "failed" ? (
                    <div className="flex flex-col items-center justify-center h-full text-center py-12 max-w-md mx-auto space-y-3">
                      <XCircle className="h-12 w-12 text-red-500" />
                      <h4 className="text-sm font-bold text-red-400">Scan Job Failed</h4>
                      <p className="text-xs text-zinc-400 font-mono bg-zinc-950 p-4 border border-zinc-900 rounded-xl max-h-40 overflow-y-auto select-text">
                        {activeRun.error_message || "Unknown scanner run error."}
                      </p>
                    </div>
                  ) : activeRun?.status === "running" || activeRun?.status === "queued" ? (
                    <div className="flex flex-col items-center justify-center h-full py-12 space-y-4">
                      <Loader2 className="h-10 w-10 text-indigo-500 animate-spin" />
                      <div className="text-center">
                        <h4 className="text-sm font-bold text-zinc-200">Executing Technical Scan</h4>
                        <p className="text-xs text-zinc-400 mt-1 max-w-xs leading-relaxed">
                          Checking SMA ordering, slope checks, and 52w bounds across Nifty 500. This takes about 2 seconds.
                        </p>
                      </div>
                    </div>
                  ) : filteredResults.length > 0 ? (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs border-collapse">
                        <thead>
                          <tr className="border-b border-zinc-800/80 text-zinc-500 uppercase tracking-wider font-semibold">
                            <th className="py-2.5 px-3 text-center w-12">Rank</th>
                            <th className="py-2.5 px-3">Symbol</th>
                            <th className="py-2.5 px-3 text-right">Price (₹)</th>
                            <th className="py-2.5 px-3 text-center">SMA (50/150/200)</th>
                            <th className="py-2.5 px-3 text-center">52w High / Low</th>
                            <th className="py-2.5 px-3 text-right">20d Avg Vol</th>
                            <th className="py-2.5 px-3 text-center w-20">RS Rating</th>
                            <th className="py-2.5 px-3 text-right w-24">Near 52w High</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-zinc-850">
                          {filteredResults.map((row: any) => (
                            <tr key={row.id} className="hover:bg-zinc-900/30 transition text-zinc-300 font-medium">
                              <td className="py-3 px-3 text-center font-bold text-zinc-400 font-mono">{row.rank}</td>
                              <td className="py-3 px-3">
                                <div className="font-bold text-zinc-100">{row.symbol}</div>
                                <div className="text-[10px] text-zinc-500 max-w-[150px] truncate">{row.name}</div>
                              </td>
                              <td className="py-3 px-3 text-right font-mono font-bold text-zinc-100">
                                {row.close_price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                              </td>
                              <td className="py-3 px-3 text-center font-mono text-[10px]">
                                <div className="flex justify-center gap-1.5">
                                  <span className="text-emerald-400">{row.sma_50.toFixed(1)}</span>
                                  <span className="text-zinc-500">&gt;</span>
                                  <span className="text-indigo-400">{row.sma_150.toFixed(1)}</span>
                                  <span className="text-zinc-500">&gt;</span>
                                  <span className="text-blue-400">{row.sma_200.toFixed(1)}</span>
                                </div>
                              </td>
                              <td className="py-3 px-3 text-center font-mono text-[10px]">
                                <div className="flex justify-center gap-3">
                                  <div>
                                    <span className="text-zinc-500 mr-1">Hi:</span>
                                    <span className="text-zinc-300">{row.high_52w.toFixed(1)}</span>
                                  </div>
                                  <div>
                                    <span className="text-zinc-500 mr-1">Lo:</span>
                                    <span className="text-zinc-300">{row.low_52w.toFixed(1)}</span>
                                  </div>
                                </div>
                              </td>
                              <td className="py-3 px-3 text-right font-mono text-zinc-400">
                                {row.avg_volume_20.toLocaleString()}
                              </td>
                              <td className="py-3 px-3 text-center font-mono">
                                <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                  row.rs_rating >= 85 
                                    ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' 
                                    : row.rs_rating >= 70 
                                      ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20' 
                                      : 'bg-zinc-800 text-zinc-400'
                                }`}>
                                  {row.rs_rating || '-'}
                                </span>
                              </td>
                              <td className="py-3 px-3 text-right font-mono font-bold text-indigo-400">
                                {(row.pct_from_52w_high * 100).toFixed(2)}%
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center h-full text-center py-12 max-w-sm mx-auto space-y-2">
                      <Search className="h-8 w-8 text-zinc-650" />
                      <h4 className="text-sm font-semibold text-zinc-400">No symbols found</h4>
                      <p className="text-xs text-zinc-500">
                        {symbolFilter 
                          ? `No stocks match filter "${symbolFilter}" for this run.` 
                          : "No stocks in Nifty 500 met all the technical criteria."
                        }
                      </p>
                    </div>
                  )}
                </div>

              </div>
              
              <div className="mt-4 text-[10px] text-zinc-500 flex justify-between border-t border-zinc-800/40 pt-3 shrink-0">
                <span>Minervini Trend Template: Price &gt; 150 & 200 SMA | 150 &gt; 200 SMA | 200 SMA Rising (1M+) | 50 &gt; 150 & 200 SMA | Price &gt; 50 SMA | &ge; 30% from 52w Low | &le; 25% from 52w High | RS Rating &ge; 70</span>
                {activeRun?.status === "succeeded" && (
                  <span>Showing {filteredResults.length} / {scanResults?.length} hits</span>
                )}
              </div>
            </div>

          </div>
        )}

      </div>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppContent />
    </QueryClientProvider>
  );
}
