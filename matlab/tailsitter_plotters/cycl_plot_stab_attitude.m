function cycl_plot_stab_attitude(ac_data, order)

    tiledlayout(3, 3, 'Padding', 'compact', 'TileSpacing', 'compact');

    att = double(string(ac_data.STAB_ATTITUDE.att));
    attref = double(string(ac_data.STAB_ATTITUDE.att_ref));

    w = double(string(ac_data.STAB_ATTITUDE.angular_rate));
    wref = double(string(ac_data.STAB_ATTITUDE.angular_rate_ref));

    wdot = double(string(ac_data.STAB_ATTITUDE.angular_accel));
    wdotref = double(string(ac_data.STAB_ATTITUDE.angular_accel_ref));

    ax1 = nexttile;
    hold on; zoom on; grid on;
    h1 = plot(ac_data.STAB_ATTITUDE.timestamp, wdotref(:,2), LineWidth=1.5);
    h2 = plot(ac_data.STAB_ATTITUDE.timestamp, wdot(:,2), LineWidth=1.5);
    xlabel('time [s]');
    ylabel('q dot [rad/s^2]');
    title('q dot');

    ax2 = nexttile;
    hold on; zoom on; grid on;
    h3 = plot(ac_data.STAB_ATTITUDE.timestamp, wref(:,2), LineWidth=1.5);
    h4 = plot(ac_data.STAB_ATTITUDE.timestamp, w(:,2), LineWidth=1.5);
    xlabel('time [s]');
    ylabel('q [rad/s]');
    title('q');

    ax3 = nexttile;
    hold on; zoom on; grid on;
    h5 = plot(ac_data.STAB_ATTITUDE.timestamp, attref(:,2), LineWidth=1.5);
    h6 = plot(ac_data.STAB_ATTITUDE.timestamp, att(:,2), LineWidth=1.5);
    xlabel('time [s]');
    ylabel('theta [rad]');
    title('theta');

    ax4 = nexttile;
    hold on; zoom on; grid on;
    h7 = plot(ac_data.STAB_ATTITUDE.timestamp, wdotref(:,1), LineWidth=1.5);
    h8 = plot(ac_data.STAB_ATTITUDE.timestamp, wdot(:,1), LineWidth=1.5);
    xlabel('time [s]');
    ylabel('p dot [rad/s^2]');
    title('p dot');

    ax5 = nexttile;
    hold on; zoom on; grid on;
    h9 = plot(ac_data.STAB_ATTITUDE.timestamp, wref(:,1), LineWidth=1.5);
    h10 = plot(ac_data.STAB_ATTITUDE.timestamp, w(:,1), LineWidth=1.5);
    xlabel('time [s]');
    ylabel('p [rad/s]');
    title('p');

    ax6 = nexttile;
    hold on; zoom on; grid on;
    h11 = plot(ac_data.STAB_ATTITUDE.timestamp, attref(:,1), LineWidth=1.5);
    h12 = plot(ac_data.STAB_ATTITUDE.timestamp, att(:,1), LineWidth=1.5);
    xlabel('time [s]');
    ylabel('phi [rad]');
    title('phi');

    ax7 = nexttile;
    hold on; zoom on; grid on;
    h13 = plot(ac_data.STAB_ATTITUDE.timestamp, wdotref(:,3), LineWidth=1.5);
    h14 = plot(ac_data.STAB_ATTITUDE.timestamp, wdot(:,3), LineWidth=1.5);
    xlabel('time [s]');
    ylabel('r dot [rad/s^2]');
    title('r dot');

    ax8 = nexttile;
    hold on; zoom on; grid on;
    h15 = plot(ac_data.STAB_ATTITUDE.timestamp, wref(:,3), LineWidth=1.5);
    h16 = plot(ac_data.STAB_ATTITUDE.timestamp, w(:,3), LineWidth=1.5);
    xlabel('time [s]');
    ylabel('r [rad/s]');
    title('r');

    ax9 = nexttile;
    hold on; zoom on; grid on;
    h17 = plot(ac_data.STAB_ATTITUDE.timestamp, attref(:,3), LineWidth=1.5);
    h18 = plot(ac_data.STAB_ATTITUDE.timestamp, att(:,3), LineWidth=1.5);
    xlabel('time [s]');
    ylabel('psi [rad]');
    title('psi');

    % flight modes
    mode_values = ac_data.ROTORCRAFT_RADIO_CONTROL.mode;
    mode_timestamps = ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp;
    cycl_draw_mode_transitions(mode_values, mode_timestamps, {ax1,ax2,ax3,ax4,ax5,ax6,ax7,ax8,ax9});
    legend(ax1, [h1,h2], {'qdot ref','qdot'});
    legend(ax2, [h3,h4], {'q ref','q'});
    legend(ax3, [h5,h6], {'theta ref','theta'});
    legend(ax4, [h7,h8], {'pdot ref','pdot'});
    legend(ax5, [h9,h10], {'p ref','p'});
    legend(ax6, [h11,h12], {'phi ref','phi'});
    legend(ax7, [h13,h14], {'rdot ref','rdot'});
    legend(ax8, [h15,h16], {'r ref','r'});
    legend(ax9, [h17,h18], {'psi ref','psi'});

    linkaxes([ax1,ax2,ax3,ax4,ax5,ax6,ax7,ax8,ax9],'x');

end