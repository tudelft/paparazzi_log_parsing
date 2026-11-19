function e = telemetry_rows(m, map, names)
%TELEMETRY_ROWS One row per sample per actuator from a message with an array per field indexed by idx
%   (ESC_TELEMETRY, SERVO_TELEMETRY: N×k matrices padded with NaN, k actuators in a message).
%   e = pprz.telemetry_rows(m, map, names)
%     m      the message struct (t, idx, ... as exported)
%     map    {message field, output field; ...}
%     names  every output field; those not in map (or not logged, including empty arrays) are NaN
%   Gives e.t and e.id (actuator index), sorted by time then index. A value whose age is 255 (older than 2.54 s,
%   stored converted as 2.55 s) is NaN: the actuator stopped reporting.
    t = double(m.t(:));
    idx = shape(m.idx, numel(t));
    [r, c] = find(~isnan(idx));
    r = r(:);
    c = c(:);
    id = reshape(idx(sub2ind(size(idx), r, c)), [], 1);
    [~, order] = sortrows([t(r), id]);
    r = r(order);
    c = c(order);
    e.t = t(r);
    e.id = id(order);
    stale = false(size(r));
    if isfield(m, 'age')
        stale = pick(m.age, numel(t), r, c) > 2.545;
    end
    for k = 1:numel(names)
        e.(names{k}) = nan(size(r));
    end
    for k = 1:size(map, 1)
        if isfield(m, map{k, 1})
            v = pick(m.(map{k, 1}), numel(t), r, c);
            v(stale) = NaN;
            e.(map{k, 2}) = v;
        end
    end
end

function M = shape(M, n)
    % N×k with one row per message (a single message or a single actuator can come out transposed).
    M = double(M);
    if size(M, 1) ~= n && size(M, 2) == n
        M = M.';
    end
end

function v = pick(M, n, r, c)
    M = shape(M, n);
    if isempty(M) || isempty(r)
        v = nan(size(r));
        return
    end
    if size(M, 2) < max(c)  % a shorter array than idx: missing values
        M(:, end + 1:max(c)) = NaN;
    end
    v = reshape(M(sub2ind(size(M), r, c)), [], 1);
end
