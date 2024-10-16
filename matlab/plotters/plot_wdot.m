function plot_wdot(ac_data, order)

    wdot = double(string(ac_data.STAB_ATTITUDE.angular_rate));
    wdotref = double(string(ac_data.STAB_ATTITUDE.angular_rate_ref));

    tiledlayout(3, 1, 'Padding', 'compact', 'TileSpacing', 'compact');

    % p dot
    ax1 = nexttile;
    hold on; zoom on;
    plot(ac_data.STAB_ATTITUDE.timestamp, wdotref(:,1));
    plot(ac_data.STAB_ATTITUDE.timestamp, wdot(:,1));
    legend('pdotref', 'pdot');
    xlabel('time(s)');
    ylabel('(rad/s^2)');
    title('pdot');
    hold off;
    
    % q dot
    ax2 = nexttile;
    hold on; zoom on;
    plot(ac_data.STAB_ATTITUDE.timestamp, wdotref(:,2));
    plot(ac_data.STAB_ATTITUDE.timestamp, wdot(:,2));
    legend('qdotref', 'qdot');
    xlabel('time(s)');
    ylabel('(rad/s^2)');
    title('qdot');
    hold off;
    
    % r dot
    ax3 = nexttile;
    hold on; zoom on;
    plot(ac_data.STAB_ATTITUDE.timestamp, wdotref(:,3));
    plot(ac_data.STAB_ATTITUDE.timestamp, wdot(:,3));
    legend('rdotref', 'rdot');
    title('rdot');
    xlabel('time(s)');
    ylabel('(rad/s^2)');
    hold off;

    linkaxes([ax1,ax2,ax3],'x')

end