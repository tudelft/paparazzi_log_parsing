function lat = c2c_latency(match)
%C2C_LATENCY Compute latency between dronetag GPS timestamps and C2C arrival.
%
%   LAT = C2C_LATENCY(MATCH)
%
%   Input:
%     match  - Output struct from match_c2c_dronetag
%
%   Output:
%     lat    - Struct with fields:
%       .time_sec       - Time axis (seconds from start of match window)
%       .latency_s      - Latency vector (seconds): c2c json_time - tag GPS time
%       .mean_s         - Mean latency (s)
%       .median_s       - Median latency (s)
%       .std_s          - Standard deviation (s)
%       .min_s          - Minimum latency (s)
%       .max_s          - Maximum latency (s)
%
%   The latency represents the total delay from when the GPS fix was recorded
%   on the aircraft (dronetag timestamp) to when the corresponding message was
%   timestamped by the Safesky C2C network (json_time in the C2C log).
%
%   Example:
%     c2c   = parse_c2c('Logs/26_02_25_EHVB');
%     tag   = parse_dronetag('Logs/26_02_25_EHVB/26_02_25__16_30_00.json');
%     match = match_c2c_dronetag(c2c, tag);
%     lat   = c2c_latency(match);
%     plot(lat.time_sec, lat.latency_s);

    if ~isstruct(match) || ~isfield(match, 'time') || ~isfield(match.c2c, 'sys_time')
        error('c2c_latency:badInput', ...
            'match must be the output of match_c2c_dronetag (needs match.c2c.sys_time).');
    end

    % match.time      = json_time (UTC GPS fix timestamp embedded in C2C payload)
    % match.c2c.sys_time = UTC-converted arrival time logged by the C2C receiver
    %                       (originally Amsterdam local time, CET = UTC+1)
    % Latency = time message was received - time GPS fix was taken
    gps_fix_time    = match.time;            % UTC, from JSON "timestamp" field
    arrival_time    = match.c2c.sys_time;    % UTC, from log line prefix

    latency_s = seconds(arrival_time - gps_fix_time);

    % Remove outliers caused by interpolation at the window edges
    valid = ~isnan(latency_s);

    lat.time_sec  = match.time_sec;
    lat.latency_s = latency_s;
    lat.mean_s    = mean(latency_s(valid));
    lat.median_s  = median(latency_s(valid));
    lat.std_s     = std(latency_s(valid));
    lat.min_s     = min(latency_s(valid));
    lat.max_s     = max(latency_s(valid));

    fprintf('C2C latency for callsign %s  (C2C arrival time − GPS fix time)\n', match.callsign);
    fprintf('  mean   = %.2f s\n', lat.mean_s);
    fprintf('  median = %.2f s\n', lat.median_s);
    fprintf('  std    = %.2f s\n', lat.std_s);
    fprintf('  range  = [%.2f, %.2f] s\n', lat.min_s, lat.max_s);
end
