function plot_gps_sats(ac_data)
%PLOT_GPS_SATS Signal strength per satellite (SVINFO) and the number of satellites heard above 30 dBHz.
%   SVINFO reports one satellite per message, so the count uses each satellite's latest report (at most 60 s old).
    m = ac_data.SVINFO;
    axs = pprz.tiles(2, 'GPS satellites');
    ids = unique(m.SVID);
    ids = reshape(ids(ids > 0), 1, []);
    for id = ids
        rows = m.SVID == id & m.CNO > 0;
        if any(rows)
            plot(axs(1), m.t(rows), double(m.CNO(rows)), '.-', 'DisplayName', sprintf('SV %d', id));
        end
    end
    ylabel(axs(1), 'C/N0 [dBHz]');
    t = unique(m.t);
    count = zeros(size(t));
    for id = ids
        rows = find(m.SVID == id);
        good = double(m.CNO(rows)) >= 30;
        k = interp1(m.t(rows), 1:numel(rows), t, 'previous');  % latest report of this satellite
        ok = ~isnan(k);
        fresh = false(size(t));
        fresh(ok) = good(k(ok)) & t(ok) - m.t(rows(k(ok))) <= 60;
        count = count + fresh;
    end
    stairs(axs(2), t, count, 'DisplayName', 'Satellites ≥ 30 dBHz');
    if pprz.has(ac_data, 'GPS_INT') && isfield(ac_data.GPS_INT, 'numsv')
        stairs(axs(2), ac_data.GPS_INT.t, double(ac_data.GPS_INT.numsv), 'DisplayName', 'Used (GPS_INT)');
        legend(axs(2), 'Location', 'best');
    end
    ylabel(axs(2), 'Satellites');
end
