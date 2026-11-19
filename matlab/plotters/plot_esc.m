function plot_esc(ac_data, idx)
%PLOT_ESC ESC telemetry per motor: speed, current, voltage, temperatures and output. Old (ESC) and new
%   (ESC_TELEMETRY) logs alike, see pprz.esc. Motors that never report rpm or current are left out, and so are
%   panels without data; plot_esc(f.data, ids) shows only these actuator indexes.
    e = pprz.esc(ac_data);
    ids = unique(e.id);
    if nargin > 1
        ids = intersect(ids, idx);
    end
    alive = arrayfun(@(i) any(nonzero(e.rpm(e.id == i)) | nonzero(e.current(e.id == i))), ids);
    ids = ids(alive);
    panels = {'rpm', 'Speed [rpm]'; 'current', 'Current [A]'; 'voltage', 'ESC voltage [V]'; ...
        'supply_voltage', 'Supply voltage [V]'; 'temperature', 'ESC temperature [°C]'; ...
        'motor_temperature', 'Motor temperature [°C]'; 'device_temperature', 'Device temperature [°C]'; ...
        'output', 'Output [%]'; 'power', 'Power [W]'};
    rows = ismember(e.id, ids);
    used = cellfun(@(fld) any(nonzero(e.(fld)(rows))), panels(:, 1));
    panels = panels(used, :);
    if isempty(panels)
        panels = {'rpm', 'Speed [rpm]'};  % nothing reported: one empty panel instead of none
    end
    axs = pprz.tiles(size(panels, 1), ['ESC (' e.source ')']);
    for p = 1:size(panels, 1)
        for i = reshape(ids, 1, [])
            in = e.id == i;
            plot(axs(p), e.t(in), e.(panels{p, 1})(in), 'DisplayName', sprintf('Motor %d', i));
        end
        ylabel(axs(p), panels{p, 2});
    end
    if ~isempty(ids)
        legend(axs(1), 'Location', 'eastoutside');
    end
end

function ok = nonzero(v)
    ok = ~isnan(v) & v ~= 0;
end
