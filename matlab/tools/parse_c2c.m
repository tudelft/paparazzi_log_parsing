function data = parse_c2c(log_dir)
%PARSE_C2C Parse Safesky C2C log .txt files grouped by callsign.
%   DATA = PARSE_C2C(LOG_DIR) reads all .txt files in LOG_DIR and returns a
%   struct with one field per callsign. Each field contains a table with
%   parsed metadata and JSON payloads. Lines without JSON payloads are skipped.

    if nargin < 1 || isempty(log_dir)
        log_dir = fullfile(pwd, 'Logs', '26_02_25_EHVB');
    end

    files = dir(fullfile(log_dir, '*.txt'));
    if isempty(files)
        error('parse_c2c:NoFiles', 'No .txt files found in %s', log_dir);
    end

    rows = struct( ...
        'source_file', {}, ...
        'line_index', {}, ...
        'sys_time', {}, ...
        'topic', {}, ...
        'json_time', {}, ...
        'payload', {}, ...
        'raw_line', {}, ...
        'parse_ok', {}, ...
        'parse_error', {});

	% Use timezone-aware NaT sentinels so table columns retain their TimeZone
	utc_nat       = NaT('TimeZone', 'UTC');
	amsterdam_nat = NaT('TimeZone', 'Europe/Amsterdam');

	row_idx = 0;

	for file_idx = 1:numel(files)
		file_path = fullfile(files(file_idx).folder, files(file_idx).name);
		fid = fopen(file_path, 'r');
		if fid < 0
			error('parse_c2c:OpenFailed', 'Unable to open %s', file_path);
		end

		line_idx = 0;
		while true
			line_raw = fgetl(fid);
			if ~ischar(line_raw)
				break;
			end

			line_idx = line_idx + 1;
			line = strtrim(line_raw);
			if isempty(line)
				continue;
			end

			json_start = strfind(line, '{');
			if isempty(json_start)
				continue;
			end

			prefix = strtrim(line(1:json_start(1)-1));
			if ~isempty(prefix) && prefix(end) == ':'
				prefix = strtrim(prefix(1:end-1));
			end

			[topic, sys_time] = parse_prefix(prefix);

			json_text = line(json_start(1):end);
			[payload, json_time, parse_ok, parse_error] = parse_payload(json_text);

			if ~isnat(json_time) && ~isnat(sys_time)
				sys_time.Year = year(json_time);
			end

			% sys_time is local Amsterdam time (CET/CEST); json_time is UTC.
			% Stamp the correct timezone on each so datetime arithmetic is correct.
			if isnat(sys_time)
				sys_time = amsterdam_nat;
			elseif isempty(sys_time.TimeZone)
				sys_time.TimeZone = 'Europe/Amsterdam';
			end
			if isnat(json_time)
				json_time = utc_nat;
			elseif isempty(json_time.TimeZone)
				json_time.TimeZone = 'UTC';
			end

			row_idx = row_idx + 1;
			rows(row_idx).source_file = files(file_idx).name;
			rows(row_idx).line_index = line_idx;
			rows(row_idx).sys_time = sys_time;
			rows(row_idx).topic = topic;
			rows(row_idx).json_time = json_time;
			rows(row_idx).payload = payload;
			rows(row_idx).raw_line = line;
			rows(row_idx).parse_ok = parse_ok;
			rows(row_idx).parse_error = parse_error;
		end

		fclose(fid);
	end

	data = group_by_callsign(struct2table(rows, 'AsArray', true));
end

function [topic, sys_time] = parse_prefix(prefix)
	topic = '';
	sys_time = NaT;

	tokens = regexp(prefix, ...
		'^(?<mon>[A-Za-z]{3})\s+(?<day>\d{1,2})\s+(?<time>\d{2}:\d{2}:\d{2})\s+(?<topic>.+)$', ...
		'names');

	if isempty(tokens)
		return;
	end

	topic = tokens.topic;
	sys_time = datetime( ...
		sprintf('%s %s %s', tokens.mon, tokens.day, tokens.time), ...
		'InputFormat', 'MMM d HH:mm:ss');
end

function [payload, json_time, parse_ok, parse_error] = parse_payload(json_text)
	payload = struct();
	json_time = NaT;
	parse_ok = false;
	parse_error = '';

	try
		payload = jsondecode(json_text);
		parse_ok = true;
	catch ME
		parse_error = ME.message;
		return;
	end

	if isstruct(payload) && isfield(payload, 'timestamp')
		json_time = parse_timestamp(payload.timestamp);
	end
end

function dt = parse_timestamp(timestamp_text)
	dt = NaT;

	if ~(ischar(timestamp_text) || isstring(timestamp_text))
		return;
	end

	% Strip trailing 'Z' and set TimeZone explicitly to avoid MATLAB's
	% inconsistent handling of literal 'Z' in InputFormat when TimeZone
	% is also specified.
	ts = char(timestamp_text);
	if ~isempty(ts) && ts(end) == 'Z'
		ts = ts(1:end-1);
	end

	try
		dt = datetime(ts, ...
			'InputFormat', 'yyyy-MM-dd''T''HH:mm:ss.SSS', ...
			'TimeZone', 'UTC');
		return;
	catch
	end

	try
		dt = datetime(ts, ...
			'InputFormat', 'yyyy-MM-dd''T''HH:mm:ss', ...
			'TimeZone', 'UTC');
	catch
		dt = NaT;
	end
end

function grouped = group_by_callsign(data_table)
	grouped = struct();
	if isempty(data_table)
		return;
	end

	callsigns = cell(height(data_table), 1);
	for idx = 1:height(data_table)
		callsigns{idx} = extract_callsign(get_payload(data_table.payload, idx));
	end

	unique_callsigns = unique(callsigns);
	for idx = 1:numel(unique_callsigns)
		callsign = unique_callsigns{idx};
		field_name = matlab.lang.makeValidName(callsign);
		sub_table = data_table(strcmp(callsigns, callsign), :);

		% Build a plain struct so that .payload can be a struct-of-vectors
		% rather than a cell array column.
		entry = struct();
		other_cols = setdiff(sub_table.Properties.VariableNames, {'payload'});
		for ci = 1:numel(other_cols)
			entry.(other_cols{ci}) = sub_table.(other_cols{ci});
		end
		entry.payload = vectorize_payloads(sub_table.payload);

		grouped.(field_name) = entry;
	end
end

function vec = vectorize_payloads(payload_col)
	% Convert a cell array or struct array of payload structs into a single
	% struct where each field is a numeric column vector (if all values are
	% numeric scalars) or a cell array of strings (if any value is a string
	% or non-scalar).
	n = numel(payload_col);
	if n == 0
		vec = {};
		return;
	end

	% Helper to index either a cell array or a struct array uniformly
	get_row = @(col, i) get_payload(col, i);

	% Collect all field names present across all payloads
	all_fields = {};
	for i = 1:n
		p = get_row(payload_col, i);
		if isstruct(p)
			all_fields = union(all_fields, fieldnames(p));
		end
	end

	if isempty(all_fields)
		vec = payload_col;
		return;
	end

	vec = struct();
	for fi = 1:numel(all_fields)
		fname = all_fields{fi};
		values = cell(n, 1);
		for i = 1:n
			p = get_row(payload_col, i);
			if isstruct(p) && isfield(p, fname)
				values{i} = p.(fname);
			else
				values{i} = [];
			end
		end
		vec.(fname) = collapse_field(values);
	end
end

function out = collapse_field(values)
	% If all entries are numeric scalars -> return as numeric column vector.
	% If all entries are char/string scalars -> return as cell array of strings.
	% If all entries are scalar structs -> recursively vectorize into a struct.
	% Otherwise -> return as cell array (preserving original values).
	n = numel(values);
	all_numeric = true;
	all_string  = true;
	all_struct  = true;
	for i = 1:n
		v = values{i};
		if isempty(v) || ~isnumeric(v) || ~isscalar(v)
			all_numeric = false;
		end
		if ~(ischar(v) || (isstring(v) && isscalar(v)))
			all_string = false;
		end
		if ~(isstruct(v) && isscalar(v))
			all_struct = false;
		end
	end

	if all_numeric
		out = cellfun(@(x) double(x), values);   % numeric column vector
	elseif all_string
		out = cellfun(@(x) char(x), values, 'UniformOutput', false);
	elseif all_struct
		% Recursively vectorize nested struct (e.g. location, groundSpeed)
		all_fields = {};
		for i = 1:n
			all_fields = union(all_fields, fieldnames(values{i}));
		end
		out = struct();
		for fi = 1:numel(all_fields)
			fname = all_fields{fi};
			sub = cell(n, 1);
			for i = 1:n
				if isfield(values{i}, fname)
					sub{i} = values{i}.(fname);
				else
					sub{i} = [];
				end
			end
			out.(fname) = collapse_field(sub);
		end
	else
		out = values;  % cell array, preserving non-uniform values as-is
	end
end

function payload = get_payload(payload_column, idx)
	if iscell(payload_column)
		payload = payload_column{idx};
	else
		payload = payload_column(idx);
	end
end

function callsign = extract_callsign(payload)
	callsign = 'unknown';
	if ~isstruct(payload) || ~isfield(payload, 'identifiers')
		return;
	end

	identifiers = payload.identifiers;
	if ~isstruct(identifiers)
		return;
	end

	for idx = 1:numel(identifiers)
		entry = identifiers(idx);
		if isfield(entry, 'system') && isfield(entry, 'key')
			if strcmpi(entry.system, 'CallSign')
				callsign = entry.key;
				return;
			end
		end
	end
end
