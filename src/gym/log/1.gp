set terminal postscript eps font "Times-Roman, 23"
set output '5g.eps'
set size 4/4.,3/4.
set key right
set key font "Times-Roman, 23"
set xlabel "time (s)"
set yrange [0:50]
set xrange [0:100]
set ylabel "throughput (Mbits/s)"
set style fill solid 0.2 noborder
set term post eps color solid enh

plot "summary.tr" u 1:2 title "Capacity (Mean 22.0 Mbps/s)" with filledcurves above x1 fc "#DDA0DD" fs solid 0.5 border rgb "slateblue1"
