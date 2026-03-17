<?php

header('Content-Type: application/json');

$plugin = 'kms.mosaic';
$cfg_file = "/boot/config/plugins/{$plugin}/{$plugin}.cfg";
$service_script = "/usr/local/emhttp/plugins/{$plugin}/scripts/kms_mosaic-service";

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

$action = $_POST['action'] ?? $_GET['action'] ?? 'status';

try {
  if ($action === 'save') {
    $cfg = read_plugin_cfg_file($cfg_file);
    $cfg['SERVICE'] = (($_POST['SERVICE'] ?? 'enable') === 'enable') ? 'enable' : 'disable';
    $cfg['WEB_SERVICE'] = (($_POST['WEB_SERVICE'] ?? 'enable') === 'enable') ? 'enable' : 'disable';
    $port = (int)($_POST['WEB_PORT'] ?? 8788);
    if ($port < 1 || $port > 65535) $port = 8788;
    $cfg['WEB_PORT'] = (string)$port;
    $cfg['CONFIG_PATH'] = trim((string)($_POST['CONFIG_PATH'] ?? '/boot/config/kms_mosaic.conf'));
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
  echo $status;
} catch (Throwable $e) {
  http_response_code(500);
  echo json_encode(['error' => $e->getMessage()]);
}
