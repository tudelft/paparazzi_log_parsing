function ok = has(d, varargin)
%HAS True when every named message is in d with at least one row: pprz.has(f.data, 'ROTORCRAFT_FP', 'AIR_DATA')
%   'MESSAGE.field' also needs that field with at least one finite value (a field newer logs added, e.g. 'AIR_DATA.wind').
    ok = true;
    for k = 1:numel(varargin)
        parts = strsplit(varargin{k}, '.');
        name = parts{1};
        if ~isfield(d, name) || ~isfield(d.(name), 't') || isempty(d.(name).t)
            ok = false;
            return
        end
        if numel(parts) > 1 && ~(isfield(d.(name), parts{2}) && any(isfinite(double(d.(name).(parts{2})(:)))))
            ok = false;
            return
        end
    end
end
