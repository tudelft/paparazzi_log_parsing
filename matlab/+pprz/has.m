function ok = has(d, varargin)
%HAS True when every named message is in d with at least one row: pprz.has(f.data, 'ROTORCRAFT_FP', 'AIR_DATA')
    ok = true;
    for k = 1:numel(varargin)
        name = varargin{k};
        if ~isfield(d, name) || ~isfield(d.(name), 't') || isempty(d.(name).t)
            ok = false;
            return
        end
    end
end
