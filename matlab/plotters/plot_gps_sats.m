function plot_gps_sats(ac_data)
%PLOT_GPS_SATS Signal strength per satellite (SVINFO) and the number of satellites above 30 dBHz.
    m = ac_data.SVINFO;
    axs = pprz.tiles(2, 'GPS satellites');
    ids = unique(m.SVID);
    ids = ids(ids > 0);
    for id = reshape(ids, 1, [])
        rows = m.SVID == id & m.CNO > 0;
        if any(rows)
            plot(axs(1), m.t(rows), double(m.CNO(rows)), '.-', 'DisplayName', sprintf('SV %d', id));
        end
    end
    ylabel(axs(1), 'C/N0 [dBHz]');
    % Satellites heard per second with a usable signal.
    edges = floor(min(m.t)):ceil(max(m.t));
    good = m.t(m.CNO >= 30);
    counts = zeros(1, max(numel(edges) - 1, 0));
    for k = 1:numel(counts)
        in = m.t >= edges(k) & m.t < edges(k + 1) & m.CNO >= 30;
        counts(k) = numel(unique(m.SVID(in)));
    end
    if ~isempty(good)
        stairs(axs(2), edges(1:end - 1), counts, 'DisplayName', 'Satellites ≥ 30 dBHz');
    end
    ylabel(axs(2), 'Satellites');
end
