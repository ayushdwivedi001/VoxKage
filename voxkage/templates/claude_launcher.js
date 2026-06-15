#!/usr/bin/env node

const http = require("http");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

// --- Resolve API Key ---
let apiKey = process.env.OPENCODE_API_KEY;
if (!apiKey) {
  try {
    const appData = process.env.APPDATA || path.join(os.homedir(), "AppData/Roaming");
    const globalKeyringPath = path.join(appData, "npm/node_modules/opencode-starter/node_modules/@napi-rs/keyring");
    const { Entry } = require(globalKeyringPath);
    apiKey = new Entry("opencode-starter", "opencode-starter").getPassword();
  } catch (err) {
    // Fail silently, maybe key is passed via other means
  }
}

if (!apiKey) {
  console.error("\nError: OPENCODE_API_KEY not found in environment or Windows Credential Manager.");
  console.error("Please run 'opencode-starter claude' once in your console to configure your key.\n");
  process.exit(1);
}

// --- Configuration ---
const modelId = process.env.VOXKAGE_CLAUDE_MODEL || "deepseek-v4-flash-free";
const backendBaseUrl = "https://opencode.ai/zen";

// --- Hash Utility ---
function hashSystemPrompt(system) {
  if (!system) return null;
  const text = typeof system === "string" ? system : system.map((s) => s.text || "").join("\n");
  if (!text.trim()) return null;
  let hash = 5381;
  for (let i = 0; i < text.length; i++) {
    hash = (hash << 5) + hash + text.charCodeAt(i);
    hash = hash & hash;
  }
  return "cache-" + Math.abs(hash).toString(36);
}

// --- Translation layer: Anthropic Request -> OpenAI Request ---
function translateRequest(body) {
  const { model, messages, system, temperature, max_tokens, top_p, stop_sequences, tools, stream } = body;
  const openAIMessages = [];

  const systemMessages = Array.isArray(system)
    ? system.map((item) => ({ role: "system", content: item.text }))
    : system
    ? [{ role: "system", content: system }]
    : [];

  if (Array.isArray(messages)) {
    for (const msg of messages) {
      if (typeof msg.content === "string") {
        openAIMessages.push({ role: msg.role, content: msg.content });
      } else if (Array.isArray(msg.content)) {
        if (msg.role === "assistant") {
          const assistantMsg = { role: "assistant", content: null };
          let text = "";
          let reasoningContent = "";
          const toolCalls = [];
          for (const part of msg.content) {
            if (part.type === "text") {
              text += (typeof part.text === "string" ? part.text : JSON.stringify(part.text)) + "\n";
            } else if (part.type === "thinking") {
              reasoningContent += (typeof part.thinking === "string" ? part.thinking : JSON.stringify(part.thinking)) + "\n";
            } else if (part.type === "tool_use") {
              toolCalls.push({
                id: part.id,
                type: "function",
                function: { name: part.name, arguments: JSON.stringify(part.input) }
              });
            }
          }
          if (text.trim()) assistantMsg.content = text.trim();
          if (reasoningContent.trim()) assistantMsg.reasoning_content = reasoningContent.trim();
          if (toolCalls.length > 0) assistantMsg.tool_calls = toolCalls;
          if (assistantMsg.content || assistantMsg.reasoning_content || assistantMsg.tool_calls) {
            openAIMessages.push(assistantMsg);
          }
        } else if (msg.role === "user") {
          let userText = "";
          const contentParts = [];
          const toolResults = [];
          for (const part of msg.content) {
            if (part.type === "text") {
              userText += (typeof part.text === "string" ? part.text : JSON.stringify(part.text)) + "\n";
            } else if (part.type === "image") {
              const src = part.source;
              if (src) {
                if (src.type === "url") {
                  contentParts.push({ type: "image_url", image_url: { url: src.url } });
                } else if (src.type === "base64") {
                  contentParts.push({ type: "image_url", image_url: { url: `data:${src.media_type};base64,${src.data}` } });
                }
              }
            } else if (part.type === "tool_result") {
              toolResults.push({
                role: "tool",
                tool_call_id: part.tool_use_id,
                content: typeof part.content === "string" ? part.content : JSON.stringify(part.content)
              });
            }
          }
          openAIMessages.push(...toolResults);
          if (contentParts.length > 0) {
            if (userText.trim()) contentParts.unshift({ type: "text", text: userText.trim() });
            openAIMessages.push({ role: "user", content: contentParts });
          } else if (userText.trim()) {
            openAIMessages.push({ role: "user", content: userText.trim() });
          }
        }
      }
    }
  }

  const data = { model: modelId, messages: [...systemMessages, ...openAIMessages] };
  if (max_tokens !== undefined) data.max_tokens = max_tokens;
  if (temperature !== undefined) data.temperature = temperature;
  if (top_p !== undefined) data.top_p = top_p;
  if (stream !== undefined) data.stream = stream;
  if (stream) data.stream_options = { include_usage: true };
  if (stop_sequences) data.stop = stop_sequences;
  if (tools) {
    data.tools = tools.map((item) => ({
      type: "function",
      function: {
        name: item.name,
        description: item.description,
        parameters: item.input_schema
      }
    }));
  }
  const cacheKey = hashSystemPrompt(system);
  if (cacheKey) data.prompt_cache_key = cacheKey;
  return data;
}

// --- Translation layer: OpenAI Response -> Anthropic Response ---
function translateResponse(completion, model) {
  const messageId = "msg_" + Date.now();
  const content = [];
  const message = completion.choices?.[0]?.message;
  if (message?.reasoning_content) {
    content.push({ type: "thinking", thinking: message.reasoning_content, signature: "" });
  }
  if (message?.content) {
    content.push({ text: message.content, type: "text" });
  }
  if (message?.tool_calls) {
    content.push(...message.tool_calls.map((item) => ({
      type: "tool_use",
      id: item.id,
      name: item.function?.name,
      input: JSON.parse(item.function?.arguments || "{}")
    })));
  }
  const finishReason = completion.choices?.[0]?.finish_reason;
  let stopReason = "end_turn";
  if (finishReason === "tool_calls") stopReason = "tool_use";
  else if (finishReason === "length") stopReason = "max_tokens";

  const result = {
    id: messageId,
    type: "message",
    role: "assistant",
    content,
    stop_reason: stopReason,
    stop_sequence: null,
    model
  };
  if (completion.usage) {
    const prompt_tokens = completion.usage.prompt_tokens || 0;
    const completion_tokens = completion.usage.completion_tokens || 0;
    const cached_tokens = completion.usage.prompt_tokens_details?.cached_tokens || 0;
    result.usage = {
      input_tokens: Math.max(0, prompt_tokens - cached_tokens),
      output_tokens: completion_tokens,
      cache_read_input_tokens: cached_tokens,
      cache_creation_input_tokens: 0
    };
  }
  return result;
}

// --- SSE Chunk Helper ---
function sseChunk(eventType, data) {
  return `event: ${eventType}\ndata: ${JSON.stringify(data)}\n\n`;
}

// --- Translation layer: OpenAI Stream -> Anthropic Stream ---
function translateStream(upstreamStream, model, res) {
  const messageId = "msg_" + Date.now();
  let contentBlockIndex = -1;
  let hasStartedTextBlock = false;
  let hasStartedThinkingBlock = false;
  let isToolUse = false;
  let currentToolCallId = null;
  let lastUsage = null;
  let finishReason = null;
  let messageStarted = false;
  let buffer = "";

  function emitSSE(eventType, data) {
    res.write(sseChunk(eventType, data));
  }

  function emitMessageStart() {
    if (messageStarted) return;
    emitSSE("message_start", {
      type: "message_start",
      message: {
        id: messageId,
        type: "message",
        role: "assistant",
        content: [],
        model,
        stop_reason: null,
        stop_sequence: null,
        usage: { input_tokens: 0, output_tokens: 0 }
      }
    });
    messageStarted = true;
  }

  function closeCurrentBlock() {
    if (isToolUse || hasStartedTextBlock || hasStartedThinkingBlock) {
      emitSSE("content_block_stop", { type: "content_block_stop", index: contentBlockIndex });
    }
  }

  function processDelta(delta, parsed) {
    if (parsed.usage) {
      const prompt_tokens = parsed.usage.prompt_tokens || 0;
      const completion_tokens = parsed.usage.completion_tokens || 0;
      const cached_tokens = parsed.usage.prompt_tokens_details?.cached_tokens || 0;
      lastUsage = {
        input_tokens: Math.max(0, prompt_tokens - cached_tokens),
        output_tokens: completion_tokens,
        cache_read_input_tokens: cached_tokens,
        cache_creation_input_tokens: 0
      };
    }
    if (parsed.choices?.[0]?.finish_reason) {
      finishReason = parsed.choices[0].finish_reason;
    }
    if (delta.tool_calls?.length > 0) {
      for (const toolCall of delta.tool_calls) {
        if (toolCall.id && toolCall.id !== currentToolCallId) {
          closeCurrentBlock();
          isToolUse = true;
          hasStartedTextBlock = false;
          hasStartedThinkingBlock = false;
          currentToolCallId = toolCall.id;
          contentBlockIndex++;
          emitMessageStart();
          emitSSE("content_block_start", {
            type: "content_block_start",
            index: contentBlockIndex,
            content_block: { type: "tool_use", id: toolCall.id, name: toolCall.function?.name, input: {} }
          });
        }
        if (toolCall.function?.arguments) {
          emitSSE("content_block_delta", {
            type: "content_block_delta",
            index: contentBlockIndex,
            delta: { type: "input_json_delta", partial_json: toolCall.function.arguments }
          });
        }
      }
      return;
    }
    if (delta.reasoning_content) {
      if (isToolUse || hasStartedTextBlock) {
        closeCurrentBlock();
        isToolUse = false;
        hasStartedTextBlock = false;
        currentToolCallId = null;
        contentBlockIndex++;
      }
      if (!hasStartedThinkingBlock) {
        if (contentBlockIndex < 0) contentBlockIndex = 0;
        emitMessageStart();
        emitSSE("content_block_start", {
          type: "content_block_start",
          index: contentBlockIndex,
          content_block: { type: "thinking", thinking: "", signature: "" }
        });
        hasStartedThinkingBlock = true;
      }
      emitSSE("content_block_delta", {
        type: "content_block_delta",
        index: contentBlockIndex,
        delta: { type: "thinking_delta", thinking: delta.reasoning_content }
      });
      return;
    }
    if (delta.content) {
      if (isToolUse || hasStartedThinkingBlock) {
        closeCurrentBlock();
        isToolUse = false;
        hasStartedThinkingBlock = false;
        currentToolCallId = null;
        contentBlockIndex++;
      }
      if (!hasStartedTextBlock) {
        if (contentBlockIndex < 0) contentBlockIndex = 0;
        emitMessageStart();
        emitSSE("content_block_start", {
          type: "content_block_start",
          index: contentBlockIndex,
          content_block: { type: "text", text: "" }
        });
        hasStartedTextBlock = true;
      }
      emitSSE("content_block_delta", {
        type: "content_block_delta",
        index: contentBlockIndex,
        delta: { type: "text_delta", text: delta.content }
      });
    }
  }

  function processLine(line) {
    if (!line.startsWith("data: ")) return;
    const data = line.slice(6).trim();
    if (data === "[DONE]") return;
    try {
      const parsed = JSON.parse(data);
      const delta = parsed.choices?.[0]?.delta;
      if (delta) processDelta(delta, parsed);
    } catch (e) {}
  }

  upstreamStream.on("data", (chunk) => {
    buffer += chunk.toString("utf8");
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (line.trim()) processLine(line);
    }
  });

  upstreamStream.on("end", () => {
    if (buffer.trim()) processLine(buffer);
    closeCurrentBlock();
    let stopReason = "end_turn";
    if (finishReason === "tool_calls") stopReason = "tool_use";
    else if (finishReason === "length") stopReason = "max_tokens";
    emitSSE("message_delta", {
      type: "message_delta",
      delta: { stop_reason: stopReason, stop_sequence: null },
      usage: lastUsage || { input_tokens: 0, output_tokens: 0 }
    });
    emitSSE("message_stop", { type: "message_stop" });
    res.end();
  });
}

// --- Start Local Proxy Server ---
const server = http.createServer((req, res) => {
  if (req.method === "HEAD") {
    res.writeHead(200);
    res.end();
    return;
  }
  if (req.method === "GET" && req.url === "/v1/models") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({
      data: [{ id: modelId, type: "model", display_name: modelId, created_at: "2025-01-01T00:00:00Z" }]
    }));
    return;
  }
  if (req.method === "POST" && req.url === "/v1/messages") {
    let rawData = "";
    req.on("data", (chunk) => { rawData += chunk; });
    req.on("end", async () => {
      try {
        const body = JSON.parse(rawData);
        const openaiReq = translateRequest(body);
        
        const upstreamUrl = `${backendBaseUrl}/v1/chat/completions`;
        const headers = {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${apiKey}`
        };

        const upstreamRes = await fetch(upstreamUrl, {
          method: "POST",
          headers,
          body: JSON.stringify(openaiReq)
        });

        if (!upstreamRes.ok) {
          const text = await upstreamRes.text();
          res.writeHead(upstreamRes.status, { "Content-Type": "application/json" });
          res.end(text);
          return;
        }

        if (openaiReq.stream && upstreamRes.body) {
          res.writeHead(200, {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
          });
          const nodeStream = require("stream").Readable.fromWeb(upstreamRes.body);
          translateStream(nodeStream, body.model, res);
        } else {
          const data = await upstreamRes.json();
          const response = translateResponse(data, body.model);
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify(response));
        }
      } catch (err) {
        res.writeHead(500, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ type: "error", error: { type: "api_error", message: err.message } }));
      }
    });
    return;
  }
  res.writeHead(404);
  res.end();
});

server.listen(0, "127.0.0.1", () => {
  const { port } = server.address();
  
  // Find Claude Binary
  const isWindows = process.platform === "win32";
  const findClaude = () => {
    const { execSync } = require("child_process");
    try {
      const output = execSync(isWindows ? "where.exe claude" : "which claude", { encoding: "utf8" });
      return output.trim().split("\n")[0].trim();
    } catch (e) {}
    const paths = isWindows ? [
      path.join(process.env.APPDATA || path.join(os.homedir(), "AppData/Roaming"), "npm/claude.cmd"),
      path.join(process.env.APPDATA || path.join(os.homedir(), "AppData/Roaming"), "npm/claude")
    ] : [
      path.join(os.homedir(), ".local/bin/claude"),
      "/usr/local/bin/claude"
    ];
    for (const p of paths) {
      if (fs.existsSync(p)) return p;
    }
    return "claude";
  };

  const claudeBinary = findClaude();
  const env = { ...process.env };
  
  // Clean Conflicting Env Vars
  const CONFLICTS = [
    "CLAUDE_CODE_USE_VERTEX", "ANTHROPIC_VERTEX_PROJECT_ID", "ANTHROPIC_VERTEX_BASE_URL",
    "CLOUD_ML_REGION", "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_AWS_BASE_URL",
    "ANTHROPIC_AWS_API_KEY", "ANTHROPIC_AWS_WORKSPACE_ID", "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"
  ];
  for (const c of CONFLICTS) {
    delete env[c];
  }

  env["ANTHROPIC_BASE_URL"] = `http://127.0.0.1:${port}`;
  env["ANTHROPIC_API_KEY"] = apiKey;
  env["ANTHROPIC_MODEL"] = modelId;
  env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "1";

  const claudeArgs = process.argv.slice(2);
  const child = spawn(claudeBinary, ["--model", modelId, ...claudeArgs], {
    stdio: "inherit",
    env,
    shell: isWindows
  });

  child.on("exit", (code) => {
    server.close();
    process.exit(code ?? 0);
  });
});
