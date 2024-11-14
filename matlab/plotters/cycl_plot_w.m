function cycl_plot_w(ac_data, order)

    w = double(string(ac_data.STAB_ATTITUDE.angular_rate));
    wref = double(string(ac_data.STAB_ATTITUDE.angular_rate_ref));

    tiledlayout(3, 1, 'Padding', 'compact', 'TileSpacing', 'compact');

    % p
    ax1 = nexttile;
    hold on; zoom on;
    plot(ac_data.STAB_ATTITUDE.timestamp, wref(:,1));
    plot(ac_data.STAB_ATTITUDE.timestamp, w(:,1));
    legend('pref', 'p');
    xlabel('time(s)');
    ylabel('(rad/s)');
    title('p');
    hold off;
    
    % q
    ax2 = nexttile;
    hold on; zoom on;
    plot(ac_data.STAB_ATTITUDE.timestamp, wref(:,2));
    plot(ac_data.STAB_ATTITUDE.timestamp, w(:,2));
    legend('qref', 'q');
    xlabel('time(s)');
    ylabel('(rad/s)');
    title('q');
    hold off;
    
    % r
    ax3 = nexttile;
    hold on; zoom on;
    plot(ac_data.STAB_ATTITUDE.timestamp, wref(:,3));
    plot(ac_data.STAB_ATTITUDE.timestamp, w(:,3));
    legend('rref', 'r');
    xlabel('time(s)');
    ylabel('(rad/s)');
    title('r');
    hold off;

    linkaxes([ax1,ax2,ax3],'x')

end