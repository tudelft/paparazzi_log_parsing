function d = crop(d, t0, t1, shift)
%CROP Keep the rows of every message with t0 <= timestamp <= t1, timestamps minus shift (default 0).
%   d = pprz.crop(f.data, 0, 60)          the first minute after takeoff
%   d = pprz.crop(ac.data, start, end, offset)
    if nargin < 4
        shift = 0;
    end
    names = fieldnames(d);
    for i = 1:numel(names)
        m = d.(names{i});
        if ~isstruct(m) || ~isfield(m, 'timestamp')
            continue
        end
        n = numel(m.timestamp);
        keep = m.timestamp >= t0 & m.timestamp <= t1;
        fields = fieldnames(m);
        for j = 1:numel(fields)
            v = m.(fields{j});
            if size(v, 1) == n
                m.(fields{j}) = v(keep, :);
            end
        end
        m.timestamp = m.timestamp - shift;
        d.(names{i}) = m;
    end
end
