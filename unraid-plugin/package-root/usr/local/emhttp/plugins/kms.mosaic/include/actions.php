<?php

$plugin = 'kms.mosaic';
$cfg_file = "/boot/config/plugins/{$plugin}/{$plugin}.cfg";
$service_script = "/usr/local/emhttp/plugins/{$plugin}/scripts/kms_mosaic-service";
$web_wrapper = "/usr/local/bin/kms_mosaic_web";

function read_plugin_cfg_file($cfg_file) {
  $defaults = [
    'SERVICE' => 'enable',
    'WEB_SERVICE' => 'enable',
    'WEB_PORT' => '8788',
    'CONFIG_PATH' => '/boot/config/kms_mosaic.conf',
  ];
  $cfg = file_exists($cfg_file) ? parse_ini_file($cfg_file) : [];
  return array_merge($defaults, is_array($cfg) ? $cfg : []);
}

function write_plugin_cfg_file($cfg_file, $cfg) {
  $lines = [];
  foreach ($cfg as $key => $value) {
    $safe = str_replace('"', '\"', (string)$value);
    $lines[] = sprintf('%s="%s"', $key, $safe);
  }
  file_put_contents($cfg_file, implode("\n", $lines) . "\n");
}

function run_service_command($service_script, $command) {
  $cmd = escapeshellcmd($service_script) . ' ' . escapeshellarg($command);
  return shell_exec($cmd);
}

function backend_base_url($cfg) {
  $port = (int)($cfg['WEB_PORT'] ?? 8788);
  if ($port < 1 || $port > 65535) $port = 8788;
  return "http://127.0.0.1:{$port}";
}

function run_web_wrapper($web_wrapper, $config_path, $args = []) {
  $parts = [escapeshellcmd($web_wrapper), '--config', escapeshellarg($config_path)];
  foreach ($args as $arg) {
    $parts[] = escapeshellarg($arg);
  }
  $cmd = implode(' ', $parts) . ' 2>&1';
  exec($cmd, $output, $code);
  if ($code !== 0) {
    throw new RuntimeException(trim(implode("\n", $output)) ?: 'kms_mosaic_web helper failed');
  }
  return implode("\n", $output);
}

function run_web_wrapper_with_temp_json($web_wrapper, $config_path, $flag, $json_text) {
  $tmp = tempnam(sys_get_temp_dir(), 'kmsmosaic_');
  if ($tmp === false) throw new RuntimeException('Unable to create temp file');
  file_put_contents($tmp, $json_text);
  try {
    return run_web_wrapper($web_wrapper, $config_path, [$flag, $tmp]);
  } finally {
    @unlink($tmp);
  }
}

function proxy_backend_request($method, $url, $body = null, $content_type = null) {
  if (!function_exists('curl_init')) {
    throw new RuntimeException('curl is required for backend proxying');
  }

  $headers = [];
  if ($content_type) $headers[] = "Content-Type: {$content_type}";
  if (!empty($_SERVER['HTTP_RANGE'])) $headers[] = "Range: {$_SERVER['HTTP_RANGE']}";

  $ch = curl_init($url);
  curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
  curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
  curl_setopt($ch, CURLOPT_HEADER, true);
  curl_setopt($ch, CURLOPT_FOLLOWLOCATION, false);
  curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 5);
  curl_setopt($ch, CURLOPT_TIMEOUT, 120);
  if ($headers) curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
  if ($body !== null) curl_setopt($ch, CURLOPT_POSTFIELDS, $body);

  $raw = curl_exec($ch);
  if ($raw === false) {
    $error = curl_error($ch);
    curl_close($ch);
    throw new RuntimeException($error ?: 'Unable to reach preview backend');
  }

  $status = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
  $header_size = curl_getinfo($ch, CURLINFO_HEADER_SIZE);
  curl_close($ch);

  $header_blob = substr($raw, 0, $header_size);
  $body_blob = substr($raw, $header_size);
  $headers_out = [];

  foreach (preg_split("/\r\n|\n|\r/", trim($header_blob)) as $line) {
    if ($line === '' || stripos($line, 'HTTP/') === 0) continue;
    $parts = explode(':', $line, 2);
    if (count($parts) !== 2) continue;
    $name = trim($parts[0]);
    $value = trim($parts[1]);
    if ($name === '') continue;
    $headers_out[strtolower($name)] = $value;
  }

  return [
    'status' => $status,
    'headers' => $headers_out,
    'body' => $body_blob,
  ];
}

function proxy_backend_json_post($url, $json_text) {
  $headers = [
    'Content-Type: application/json',
    'Content-Length: ' . strlen($json_text),
  ];
  $context = stream_context_create([
    'http' => [
      'method' => 'POST',
      'header' => implode("\r\n", $headers) . "\r\n",
      'content' => $json_text,
      'ignore_errors' => true,
      'timeout' => 120,
    ],
  ]);

  $body = @file_get_contents($url, false, $context);
  if ($body === false) {
    throw new RuntimeException('Unable to reach preview backend');
  }

  $status = 200;
  $content_type = 'application/json';
  $response_headers = $http_response_header ?? [];
  foreach ($response_headers as $line) {
    if (preg_match('#^HTTP/\S+\s+(\d{3})#', $line, $m)) {
      $status = (int)$m[1];
      continue;
    }
    if (stripos($line, 'Content-Type:') === 0) {
      $content_type = trim(substr($line, strlen('Content-Type:')));
    }
  }

  return [
    'status' => $status,
    'headers' => ['content-type' => $content_type],
    'body' => $body,
  ];
}

function output_proxy_response($result, $allowed_headers = []) {
  http_response_code($result['status']);
  foreach ($allowed_headers as $name) {
    $key = strtolower($name);
    if (isset($result['headers'][$key])) {
      header($name . ': ' . $result['headers'][$key]);
    }
  }
  echo $result['body'];
  exit;
}

$action = $_POST['action'] ?? $_GET['action'] ?? 'status';

try {
  $cfg = read_plugin_cfg_file($cfg_file);
  $backend = backend_base_url($cfg);

  if ($action === 'backend_html') {
    header('Content-Type: text/html; charset=utf-8');
    echo run_web_wrapper($web_wrapper, $cfg['CONFIG_PATH'], ['--print-html']);
    exit;
  }

  if ($action === 'backend_state') {
    header('Content-Type: application/json');
    echo run_web_wrapper($web_wrapper, $cfg['CONFIG_PATH'], ['--dump-state']);
    exit;
  }

  if ($action === 'backend_config') {
    header('Content-Type: application/json');
    echo run_web_wrapper_with_temp_json(
      $web_wrapper,
      $cfg['CONFIG_PATH'],
      '--write-state-json',
      file_get_contents('php://input')
    );
    exit;
  }

  if ($action === 'backend_raw_config') {
    header('Content-Type: application/json');
    echo run_web_wrapper_with_temp_json(
      $web_wrapper,
      $cfg['CONFIG_PATH'],
      '--write-raw-json',
      file_get_contents('php://input')
    );
    exit;
  }

  if ($action === 'backend_webrtc_offer') {
    $json_payload = $_POST['payload'] ?? $_GET['payload'] ?? file_get_contents('php://input');
    $result = proxy_backend_json_post($backend . '/api/webrtc-offer', (string)$json_payload);
    output_proxy_response($result, ['Content-Type']);
  }

  if ($action === 'backend_media') {
    $path = (string)($_GET['path'] ?? '');
    $result = proxy_backend_request('GET', $backend . '/api/media?path=' . rawurlencode($path));
    output_proxy_response($result, ['Content-Type', 'Content-Length', 'Accept-Ranges', 'Content-Range', 'Cache-Control']);
  }

  if ($action === 'save') {
    $request = array_merge($_GET, $_POST);
    $cfg['SERVICE'] = (($request['SERVICE'] ?? 'enable') === 'enable') ? 'enable' : 'disable';
    $cfg['WEB_SERVICE'] = (($request['WEB_SERVICE'] ?? 'enable') === 'enable') ? 'enable' : 'disable';
    $port = (int)($request['WEB_PORT'] ?? 8788);
    if ($port < 1 || $port > 65535) $port = 8788;
    $cfg['WEB_PORT'] = (string)$port;
    $cfg['CONFIG_PATH'] = trim((string)($request['CONFIG_PATH'] ?? '/boot/config/kms_mosaic.conf'));
    if ($cfg['CONFIG_PATH'] === '') $cfg['CONFIG_PATH'] = '/boot/config/kms_mosaic.conf';
    write_plugin_cfg_file($cfg_file, $cfg);
    run_service_command($service_script, 'restart');
  } elseif ($action === 'start' || $action === 'stop' || $action === 'restart') {
    run_service_command($service_script, $action);
  }

  $status = run_service_command($service_script, 'status');
  if (!$status) {
    throw new RuntimeException('Unable to read service status');
  }

  header('Content-Type: application/json');
  echo $status;
} catch (Throwable $e) {
  http_response_code(500);
  header('Content-Type: application/json');
  echo json_encode(['error' => $e->getMessage()]);
}
